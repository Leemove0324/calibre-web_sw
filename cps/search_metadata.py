# -*- coding: utf-8 -*-

#  This file is part of the Calibre-Web (https://github.com/janeczku/calibre-web)
#    Copyright (C) 2021 OzzieIsaacs
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program. If not, see <http://www.gnu.org/licenses/>.

import concurrent.futures
import importlib
import inspect
import json
import os
import sys
from urllib.parse import urlsplit

from flask import Blueprint, request, url_for, make_response, jsonify, abort
from .cw_login import current_user
from flask_babel import get_locale
from sqlalchemy.exc import InvalidRequestError, OperationalError
from sqlalchemy.orm.attributes import flag_modified

from cps.services import metadata_settings
from cps.services.Metadata import Metadata
from cps.services import metadata_history
from cps.services.worker import WorkerThread, STAT_WAITING, STAT_STARTED
from cps.services.book_search import rank_records  # F3: 결과 통합·순위화
from cps.tasks.metadata_fetch import TaskFetchMetadata
from . import calibre_db, constants, logger, ub, web_server
from .render_template import render_title_template
from .usermanagement import user_login_required
# F2: 검색어 정리
from cps.services.query_analyzer import analyze_query


meta = Blueprint("metadata", __name__)

log = logger.create()


def _safe_metadata_url(value):
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if not value:
        return ""
    parsed = urlsplit(value)
    if parsed.scheme in ("http", "https"):
        return value
    if not parsed.scheme and value.startswith("/"):
        return value
    return ""


def _sanitize_metadata_record(record):
    record["url"] = _safe_metadata_url(record.get("url"))
    record["cover"] = _safe_metadata_url(record.get("cover"))
    source = record.get("source")
    if isinstance(source, dict):
        source["link"] = _safe_metadata_url(source.get("link"))
    return record


def _serialize_metadata_records(records):
    return [
        _sanitize_metadata_record(asdict(record))
        for record in (records or [])
        if record
    ]

try:
    from dataclasses import asdict
except ImportError:
    log.info('*** "dataclasses" is needed for calibre-web to run. Please install it using pip: "pip install dataclasses" ***')
    print('*** "dataclasses" is needed for calibre-web to run. Please install it using pip: "pip install dataclasses" ***')
    web_server.stop(True)
    sys.exit(6)

new_list = list()
meta_dir = os.path.join(constants.BASE_DIR, "cps", "metadata_provider")
modules = os.listdir(os.path.join(constants.BASE_DIR, "cps", "metadata_provider"))
for f in modules:
    if os.path.isfile(os.path.join(meta_dir, f)) and not f.endswith("__init__.py"):
        a = os.path.basename(f)[:-3]
        try:
            importlib.import_module("cps.metadata_provider." + a)
            new_list.append(a)
        except (IndentationError, SyntaxError) as e:
            log.error("Syntax error for metadata source: {} - {}".format(a, e))
        except ImportError as e:
            log.debug("Import error for metadata source: {} - {}".format(a, e))


def list_classes(provider_list):
    classes = list()
    for element in provider_list:
        for name, obj in inspect.getmembers(
            sys.modules["cps.metadata_provider." + element]
        ):
            if (
                inspect.isclass(obj)
                and name != "Metadata"
                and issubclass(obj, Metadata)
            ):
                classes.append(obj())
    return classes


cl = list_classes(new_list)


@meta.route("/metadata/provider")
@user_login_required
def metadata_provider():
    active = current_user.view_settings.get("metadata", {})
    provider = list()
    for c in cl:
        ac = active.get(c.__id__, True)
        # F8: "configured" is False while a provider lacks its API key; the page shows it disabled
        provider.append(
            {"name": c.__name__, "active": ac, "initial": ac, "id": c.__id__,
             "configured": c.is_configured()}
        )
    return make_response(jsonify(provider))


@meta.route("/metadata/provider", methods=["POST"])
@meta.route("/metadata/provider/<prov_name>", methods=["POST"])
@user_login_required
def metadata_change_active_provider(prov_name):
    new_state = request.get_json()
    active = current_user.view_settings.get("metadata", {})
    active[new_state["id"]] = new_state["value"]
    current_user.view_settings["metadata"] = active
    try:
        try:
            flag_modified(current_user, "view_settings")
        except AttributeError:
            pass
        ub.session.commit()
    except (InvalidRequestError, OperationalError):
        log.error("Invalid request received: {}".format(request))
        return "Invalid request", 400
    if "initial" in new_state and prov_name:
        data = []
        provider = next((c for c in cl if c.__id__ == prov_name), None)
        if provider is not None and provider.is_configured():
            data = provider.search(new_state.get("query", ""))
        return make_response(jsonify(_serialize_metadata_records(data)))
    return ""


@meta.route("/metadata/search", methods=["POST"])
@user_login_required
def metadata_search():
    query = request.form.to_dict().get("query")
    data = list()
    active = current_user.view_settings.get("metadata", {})
    locale = get_locale()
    if query:
        # F2: 판 표기·인용 부호를 지운 검색어 (맞는 ISBN이면 ISBN-13)
        analysis = analyze_query(query)
        query = analysis.isbn13 or analysis.title or query
        static_cover = url_for("static", filename="generic_cover.jpg")
        # ret = cl[0].search(query, static_cover, locale)
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            meta = {
                executor.submit(c.search, query, static_cover, locale): c
                for c in cl
                if active.get(c.__id__, True) and c.is_configured()  # F8: skip providers without a key
            }
            for future in concurrent.futures.as_completed(meta):
                data.extend(r for r in (future.result() or []) if r)
        # F3: 같은 ISBN 결과를 합치고 점수 높은 순으로 정렬
        ranked = rank_records(data, isbn=analysis.isbn13, title=analysis.title)
        data = []
        for record, score in ranked:
            item = _serialize_metadata_records([record])[0]
            item["score"] = score
            data.append(item)
    return make_response(jsonify(data))


# F6: fetch metadata for many books at once in a background task
BULK_MAX_BOOKS = 200


@meta.route("/metadata/bulk", methods=["POST"])
@user_login_required
def metadata_bulk_fetch():
    if not (current_user.role_edit() or current_user.role_admin()):
        abort(403)
    data = request.get_json(silent=True) or {}
    try:
        book_ids = [int(book_id) for book_id in data.get("book_ids", [])]
    except (TypeError, ValueError):
        return make_response(jsonify(error="book_ids must be a list of numbers"), 400)
    if not book_ids:
        return make_response(jsonify(error="No books selected"), 400)
    if len(book_ids) > BULK_MAX_BOOKS:
        return make_response(jsonify(error="Select at most {} books".format(BULK_MAX_BOOKS)), 400)
    # same provider on/off choice as /metadata/search (the user's metadata view settings)
    active = current_user.view_settings.get("metadata", {})
    provider_ids = [c.__id__ for c in cl if active.get(c.__id__, True)]
    # F8: the threshold comes from the admin page, read when the task is created
    task = TaskFetchMetadata(book_ids, current_user.id, provider_ids=provider_ids,
                             threshold=metadata_settings.threshold(),
                             recorder_factory=metadata_history.make_recorder_factory())  # F7: history
    WorkerThread.add(current_user.name, task)
    return make_response(jsonify(task_id=str(task.id), books=len(task.book_ids)), 202)


@meta.route("/metadata/bulk/<task_id>", methods=["GET"])
@user_login_required
def metadata_bulk_status(task_id):
    for queued in WorkerThread.get_instance().tasks:
        task = queued.task
        if str(task.id) != task_id or not isinstance(task, TaskFetchMetadata):
            continue
        if queued.user != current_user.name and not current_user.role_admin():
            abort(403)
        return make_response(jsonify(task_id=str(task.id), status=task.stat, progress=task.progress,
                                     counts=task.counts(), results=task.results))
    abort(404)


# F7 (#9): per-book results of a bulk fetch, its change history, and undo

def _find_bulk_task(task_id):
    """(task, user name) of a bulk fetch still known to the worker, else (None, None)."""
    for queued in WorkerThread.get_instance().tasks:
        if str(queued.task.id) == task_id and isinstance(queued.task, TaskFetchMetadata):
            return queued.task, queued.user
    return None, None


def _check_bulk_access(task_id):
    """Returns the in-memory task (or None); aborts with 404/403 like the status route."""
    task, user_name = _find_bulk_task(task_id)
    owner_id = metadata_history.task_owner(task_id)
    if task is None and owner_id is None:
        abort(404)
    if current_user.role_admin():
        return task
    if task is not None and user_name == current_user.name:
        return task
    if task is None and owner_id == current_user.id:
        return task
    abort(403)


@meta.route("/metadata/bulk/<task_id>/view", methods=["GET"])
@user_login_required
def metadata_bulk_view(task_id):
    task = _check_bulk_access(task_id)
    rows = metadata_history.task_history(task_id)
    books = {}
    for entry in (task.results if task is not None else []):
        books[entry["book_id"]] = {"book_id": entry["book_id"], "title": entry.get("title", ""),
                                   "result": entry["result"], "score": entry.get("score"),
                                   "source": entry.get("source", ""), "error": entry.get("error", ""),
                                   "changes": []}
    undoable = 0
    found_books = {}
    for row in rows:
        if row.book_id not in found_books:
            found_books[row.book_id] = calibre_db.get_book(row.book_id)
        found = found_books[row.book_id]
        book = books.get(row.book_id)
        if book is None:
            book = books[row.book_id] = {"book_id": row.book_id, "title": found.title if found else "",
                                         "result": "applied", "score": row.score, "source": row.source,
                                         "error": "", "changes": []}
        # kept: changed again after the task, so an undo leaves it alone
        kept = not row.reverted and found is not None and metadata_history.changed_since(row, found)
        undoable += 1 if not row.reverted and not kept else 0
        book["changes"].append({"field": row.field, "old_value": row.old_value, "new_value": row.new_value,
                                "reverted": row.reverted, "kept": kept})
    running = task is not None and task.stat in (STAT_WAITING, STAT_STARTED)
    can_undo = undoable > 0 and not running and (current_user.role_edit() or current_user.role_admin())
    return render_title_template("metadata_bulk.html", title="Metadata fetch results", page="metadata_bulk",
                                 task_id=task_id, task=task, books=list(books.values()), running=running,
                                 can_undo=can_undo, counts=task.counts() if task is not None else None)


@meta.route("/metadata/bulk/<task_id>/revert", methods=["POST"])
@user_login_required
def metadata_bulk_revert(task_id):
    if not (current_user.role_edit() or current_user.role_admin()):
        abort(403)
    task = _check_bulk_access(task_id)
    if task is not None and task.stat in (STAT_WAITING, STAT_STARTED):
        return make_response(jsonify(error="The task is still running"), 409)
    summary = metadata_history.revert_task(task_id, calibre_db)
    return make_response(jsonify(task_id=task_id, **summary))


@meta.route("/metadata/bulk/history", methods=["GET"])
@user_login_required
def metadata_bulk_history():
    """The user's bulk fetches that changed something (admins see everyone's), newest first."""
    tasks = metadata_history.list_tasks(None if current_user.role_admin() else current_user.id)
    names = dict(ub.session.query(ub.User.id, ub.User.name).all())
    for entry in tasks:
        entry["user_name"] = names.get(entry["user_id"], entry["user_id"])
    return render_title_template("metadata_bulk_history.html", title="Metadata fetch history",
                                 page="metadata_bulk_history", tasks=tasks)
