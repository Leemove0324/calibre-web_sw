# -*- coding: utf-8 -*-

#  This file is part of the Calibre-Web (https://github.com/janeczku/calibre-web)
#    Copyright (C) 2026 Calibre-Web contributors
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

"""F7 (#9): history of bulk metadata changes, and undoing a whole task.

Recording, done by TaskFetchMetadata (F6) through a Recorder, one per book:
  1. recorder(book, field, old, new) right BEFORE a field is changed: the row is committed to
     app.db first, so no change is ever made without a record. For a cover that already exists,
     cover.jpg is copied to a backup file first.
  2. cancel(book, field) when the write then changed nothing: the row is dropped.
  3. confirm(book) after metadata.db was committed: new_value is set to what metadata.db really
     holds now (e.g. the description after HTML cleaning), and a copy of an applied cover is kept,
     so an undo can tell whether someone changed the field again later.
  4. discard() when the commit failed: the rows are dropped and a downloaded cover file is put back.

Undoing (revert_task): every not-yet-reverted row of the task is put back, newest first.
A field someone changed again after the task is left alone and reported as "skipped", so an undo
never destroys a later edit. Undone rows are marked reverted, so undoing twice is harmless.
"""

import filecmp
import html
import os
import re
import shutil
from datetime import datetime, timezone

from cps import config, db, logger, ub
from cps.tasks import metadata_fetch as mf

log = logger.create()

REVERTED = "reverted"
SKIPPED = "skipped"      # changed again after the task: left alone
MISSING = "missing"      # the book no longer exists
FAILED = "failed"        # writing the old value failed
RESULTS = (REVERTED, SKIPPED, MISSING, FAILED)


def backup_dir(task_id):
    """Cover copies of one task. They live next to app.db, not inside the Calibre library."""
    safe = re.sub(r"[^0-9A-Za-z_-]", "_", str(task_id))
    return os.path.join(os.path.dirname(os.path.abspath(ub.app_DB_path)), "metadata_history", safe)


def _cover_path(book_path):
    return os.path.join(config.get_book_path(), book_path, "cover.jpg")


def _applied_cover_copy(task_id, book_id):
    return os.path.join(backup_dir(task_id), "{}.applied.jpg".format(book_id))


def _comments_text(value):
    """The words of a description without its HTML: saving the normal edit page rewrites the markup
    (e.g. <b> becomes <strong>) even when nobody touched the description."""
    text = html.unescape(re.sub(r"<[^>]*>", " ", value))
    return " ".join(text.split())


def _same_value(field, current, recorded):
    """Compare a field's current value with the recorded one.
    Tags: order does not matter. Description: only the text counts, not the markup."""
    current = current or ""
    recorded = recorded or ""
    if field == mf.FIELD_TAGS:
        return sorted(t for t in current.split(",") if t) == sorted(t for t in recorded.split(",") if t)
    if field == mf.FIELD_COMMENTS:
        return _comments_text(current) == _comments_text(recorded)
    return current == recorded


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------

class Recorder:
    """History of one book in one task. See the module docstring for the call order."""

    def __init__(self, task_id, user_id, source="", score=None, session=None):
        self.task_id = str(task_id)
        self.user_id = user_id
        self.source = source or ""
        self.score = score
        self.session = session or ub.get_new_session_instance()
        self.rows = []          # MetadataFetchHistory rows written for this book
        self.book_path = None

    def __call__(self, book, field, old, new):
        self.book_path = book.path
        old_value = old or ""
        if field == mf.FIELD_COVER:
            old_value = self._backup_cover(book) if old else ""
        row = ub.MetadataFetchHistory(task_id=self.task_id, user_id=self.user_id, book_id=book.id,
                                      field=field, old_value=old_value, new_value=new or "",
                                      source=self.source, score=self.score, reverted=False)
        self.session.add(row)
        try:
            self.session.commit()  # history first, then the change
        except Exception:
            self.session.rollback()
            raise
        self.rows.append(row)

    def _backup_cover(self, book):
        source = _cover_path(book.path)
        if not os.path.isfile(source):
            return ""
        os.makedirs(backup_dir(self.task_id), exist_ok=True)
        target = os.path.join(backup_dir(self.task_id), "{}.jpg".format(book.id))
        shutil.copyfile(source, target)
        return target

    def cancel(self, book, field):
        """The write changed nothing: drop the row just recorded for this field."""
        for row in reversed(self.rows):
            if row.field == field:
                self.rows.remove(row)
                self._drop_backup(row)
                self.session.delete(row)
                self.session.commit()
                return

    def confirm(self, book):
        """metadata.db was committed: store each field as it is stored now."""
        for row in self.rows:
            if row.field == mf.FIELD_COVER:
                cover = _cover_path(book.path)
                if os.path.isfile(cover):
                    os.makedirs(backup_dir(self.task_id), exist_ok=True)
                    shutil.copyfile(cover, _applied_cover_copy(self.task_id, book.id))
            else:
                row.new_value = mf.current_value(book, row.field)
        self.session.commit()

    def discard(self):
        """The change was rolled back: drop the rows and put a downloaded cover file back."""
        for row in self.rows:
            if row.field == mf.FIELD_COVER and self.book_path is not None:
                _put_cover_file_back(self.book_path, row.old_value)
            self._drop_backup(row)
            self.session.delete(row)
        self.session.commit()
        self.rows = []

    @staticmethod
    def _drop_backup(row):
        if row.field == mf.FIELD_COVER and row.old_value and os.path.isfile(row.old_value):
            os.remove(row.old_value)


class RecorderFactory:
    """recorder_factory for TaskFetchMetadata: all recorders of a task share one app.db session,
    opened on first use. The task calls close() when it ends."""

    def __init__(self, session=None):
        self.session = session
        self._own = session is None

    def __call__(self, task_id, user_id, source="", score=None):
        if self.session is None:
            self.session = ub.get_new_session_instance()
        return Recorder(task_id, user_id, source=source, score=score, session=self.session)

    def close(self):
        if self._own and self.session is not None:
            engine = self.session.get_bind()
            self.session.remove()
            engine.dispose()
            self.session = None


def make_recorder_factory(session=None):
    return RecorderFactory(session)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def task_history(task_id, session=None):
    """All history rows of a task, oldest first."""
    session = session or ub.session
    return (session.query(ub.MetadataFetchHistory)
            .filter(ub.MetadataFetchHistory.task_id == str(task_id))
            .order_by(ub.MetadataFetchHistory.id).all())


def list_tasks(user_id=None, session=None, limit=100):
    """Tasks that have history, newest first: [{"task_id", "user_id", "applied_at", "books",
    "changes", "reverted"}]. user_id=None lists every user's tasks."""
    from sqlalchemy import func, case
    session = session or ub.session
    history = ub.MetadataFetchHistory
    query = session.query(history.task_id, func.min(history.user_id), func.min(history.applied_at),
                          func.count(func.distinct(history.book_id)), func.count(history.id),
                          func.sum(case((history.reverted.is_(True), 1), else_=0)))
    if user_id is not None:
        query = query.filter(history.user_id == user_id)
    rows = query.group_by(history.task_id).order_by(func.min(history.id).desc()).limit(limit).all()
    return [{"task_id": r[0], "user_id": r[1], "applied_at": r[2], "books": r[3], "changes": r[4],
             "reverted": int(r[5] or 0)} for r in rows]


def task_owner(task_id, session=None):
    """user_id of the task's history, or None when the task has none."""
    session = session or ub.session
    row = (session.query(ub.MetadataFetchHistory.user_id)
           .filter(ub.MetadataFetchHistory.task_id == str(task_id)).first())
    return row[0] if row else None


# ---------------------------------------------------------------------------
# Undoing
# ---------------------------------------------------------------------------

def _put_cover_file_back(book_path, backup):
    """Put cover.jpg back as it was: the backup copy, or no file when there was no cover."""
    target = _cover_path(book_path)
    if backup and os.path.isfile(backup):
        shutil.copyfile(backup, target)
        return True
    if os.path.isfile(target):
        os.remove(target)
    return False


def _cover_changed_since(row, book):
    """True when the cover is no longer the one the task applied."""
    if not book.has_cover:
        return True
    applied = _applied_cover_copy(row.task_id, book.id)
    current = _cover_path(book.path)
    if not os.path.isfile(applied) or not os.path.isfile(current):
        return True
    return not filecmp.cmp(applied, current, shallow=False)


def changed_since(row, book):
    """True when a person changed the field after the task, so an undo would leave it alone."""
    if row.field == mf.FIELD_COVER:
        return _cover_changed_since(row, book)
    return not _same_value(row.field, mf.current_value(book, row.field), row.new_value)


def _revert_row(calibre_db, row):
    book = calibre_db.session.query(db.Books).filter(db.Books.id == row.book_id).one_or_none()
    if book is None:
        return MISSING
    if changed_since(row, book):
        return SKIPPED
    if row.field == mf.FIELD_COVER:
        from cps import helper
        book.has_cover = 1 if _put_cover_file_back(book.path, row.old_value) else 0
        helper.replace_cover_thumbnail_cache(book.id)
    else:
        if row.field == mf.FIELD_COMMENTS and not row.old_value and book.comments:
            calibre_db.session.delete(book.comments[0])  # there was no description row at all
        else:
            mf._write_field(calibre_db.session, book, row.field, row.old_value or "")
    book.last_modified = datetime.now(timezone.utc)
    calibre_db.set_metadata_dirty(book.id)
    calibre_db.session.commit()
    return REVERTED


def revert_task(task_id, calibre_db, session=None):
    """Undo every not-yet-reverted change of a task, newest first.

    calibre_db: a db.CalibreDB (its session writes metadata.db).
    Returns {"reverted": n, "skipped": n, "missing": n, "failed": n,
             "rows": [{"id", "book_id", "field", "result"}]}.
    Skipped and failed rows stay not-reverted, so they show up again on the next undo.
    """
    session = session or ub.session
    rows = (session.query(ub.MetadataFetchHistory)
            .filter(ub.MetadataFetchHistory.task_id == str(task_id),
                    ub.MetadataFetchHistory.reverted.is_(False))
            .order_by(ub.MetadataFetchHistory.id.desc()).all())
    summary = {result: 0 for result in RESULTS}
    summary["rows"] = []
    for row in rows:
        calibre_db.create_functions(config)  # books UPDATE trigger needs title_sort()
        try:
            result = _revert_row(calibre_db, row)
        except Exception as ex:
            calibre_db.session.rollback()
            log.error("Reverting history row %s failed: %s", row.id, ex)
            result = FAILED
        if result in (REVERTED, MISSING):
            row.reverted = True
            session.commit()
        summary[result] += 1
        summary["rows"].append({"id": row.id, "book_id": row.book_id, "field": row.field, "result": result})
    log.info("Reverted metadata task %s: %s reverted, %s skipped, %s missing, %s failed", task_id,
             summary[REVERTED], summary[SKIPPED], summary[MISSING], summary[FAILED])
    return summary
