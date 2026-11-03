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

"""F6: fetch metadata for many books in one background task.

Flow per book: build a query (ISBN first, else title + authors) -> search the
providers -> take the best scored candidate -> if its score reaches the
threshold, fill the book's EMPTY fields with it; otherwise leave the book
"pending review" for a person to check.

Title and authors are never changed here: changing them renames the book
folder on disk (helper.update_dir_structure), which is too risky in bulk.
"""

import concurrent.futures
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher

from babel import Locale
from flask_babel import lazy_gettext as N_

from cps import app, db, logger
from cps.services.worker import CalibreTask, STAT_CANCELLED, STAT_ENDED

log = logger.create()

# Fields the bulk task may fill. Title, authors and series are left out on purpose.
FIELD_PUBLISHER = "publisher"
FIELD_PUBDATE = "pubdate"
FIELD_COMMENTS = "comments"
FIELD_ISBN = "isbn"
FIELD_TAGS = "tags"
FIELD_COVER = "cover"
DEFAULT_FIELDS = (FIELD_PUBLISHER, FIELD_PUBDATE, FIELD_COMMENTS, FIELD_ISBN, FIELD_TAGS, FIELD_COVER)

DEFAULT_THRESHOLD = 0.85   # theta from the project plan, tuned later with the dev sample
SEARCH_WORKERS = 4         # books searched at the same time; DB writes stay one at a time

RESULT_APPLIED = "applied"
RESULT_PENDING = "pending"     # best candidate below the threshold: a person decides
RESULT_NO_MATCH = "no_match"   # no candidate at all
RESULT_UNCHANGED = "unchanged" # matched, but every field was already filled
RESULT_FAILED = "failed"


# ---------------------------------------------------------------------------
# Searching and scoring
# ---------------------------------------------------------------------------

def _normalize(text):
    """Lower-case and keep only letters and digits (Hangul included)."""
    return re.sub(r"[\W_]+", "", (text or "").lower())


def _clean_isbn(value):
    return re.sub(r"[^0-9Xx]", "", value or "").upper()


def temporary_score(item, record):
    """Placeholder score in [0, 1] until F3 (result merge and ranking) is ready.

    An exact ISBN match scores 1.0. Otherwise 70% title similarity plus 30%
    author overlap.
    """
    isbn = _clean_isbn(item.get("isbn"))
    if isbn and _clean_isbn((record.identifiers or {}).get("isbn")) == isbn:
        return 1.0
    title_score = SequenceMatcher(None, _normalize(item.get("title")), _normalize(record.title)).ratio()
    wanted = {_normalize(a) for a in item.get("authors", []) if a}
    found = {_normalize(a) for a in (record.authors or []) if a}
    author_score = len(wanted & found) / len(wanted) if wanted else 0.0
    return round(0.7 * title_score + 0.3 * author_score, 4)


def default_search(item):
    """Search every active provider for one book and return [(record, score)], best first.

    Runs outside a web request, so it calls the provider classes directly instead of
    the /metadata/search route. F3 will replace this with its search_book() function.
    """
    from cps.search_metadata import cl  # late import: search_metadata imports this module

    query = item.get("isbn") or " ".join([item.get("title", "")] + item.get("authors", [])).strip()
    if not query:
        return []
    locale = Locale("ko")  # providers expect a babel Locale (they read .language from it)
    records = []
    for provider in cl:
        if not provider.active:
            continue
        try:
            found = provider.search(query, "", locale)
        except Exception as ex:  # one broken provider must not stop the whole task
            log.warning("Metadata provider %s failed: %s", provider.__id__, ex)
            continue
        records.extend(r for r in (found or []) if r)
    scored = [(record, temporary_score(item, record)) for record in records]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored


# ---------------------------------------------------------------------------
# Applying a record to a book (works inside a background task)
# ---------------------------------------------------------------------------

def _parse_pubdate(value):
    """Accept 'YYYY-MM-DD', 'YYYY-MM' or 'YYYY' (anything after the date is ignored).
    Return None when it cannot be read."""
    text = (value or "").strip()
    for fmt, length in (("%Y-%m-%d", 10), ("%Y-%m", 7), ("%Y", 4)):
        try:
            return datetime.strptime(text[:length], fmt)
        except ValueError:
            continue
    return None


def _book_isbn(book):
    for identifier in book.identifiers:
        if identifier.type.lower() == "isbn":
            return identifier.val
    return ""


def current_value(book, field):
    """The book's value for a field as plain text, '' when empty. Used for history and checks."""
    if field == FIELD_PUBLISHER:
        return book.publishers[0].name if book.publishers else ""
    if field == FIELD_PUBDATE:
        if book.pubdate is None or book.pubdate.year <= db.Books.DEFAULT_PUBDATE.year:
            return ""
        return book.pubdate.strftime("%Y-%m-%d")
    if field == FIELD_COMMENTS:
        return book.comments[0].text if book.comments and book.comments[0].text else ""
    if field == FIELD_ISBN:
        return _book_isbn(book)
    if field == FIELD_TAGS:
        return ",".join(tag.name for tag in book.tags)
    if field == FIELD_COVER:
        return "1" if book.has_cover else ""
    raise ValueError("Unknown field: {}".format(field))


def _new_value(record, field):
    """The candidate's value for a field as plain text, '' when it has none."""
    if field == FIELD_PUBLISHER:
        return (record.publisher or "").strip()
    if field == FIELD_PUBDATE:
        parsed = _parse_pubdate(record.publishedDate)
        return parsed.strftime("%Y-%m-%d") if parsed else ""
    if field == FIELD_COMMENTS:
        return (record.description or "").strip()
    if field == FIELD_ISBN:
        return _clean_isbn((record.identifiers or {}).get("isbn"))
    if field == FIELD_TAGS:
        return ",".join(t.strip() for t in (record.tags or []) if t and t.strip())
    if field == FIELD_COVER:
        cover = record.cover or ""
        return cover if cover.startswith(("http://", "https://")) else ""
    raise ValueError("Unknown field: {}".format(field))


def _write_field(session, book, field, value):
    """Write one field into metadata.db through the task's own session. Returns True if changed."""
    from cps.editbooks import modify_database_object  # late import: editbooks is a web module
    from cps.clean_html import clean_string

    if field == FIELD_PUBLISHER:
        return modify_database_object([value] if value else [], book.publishers, db.Publishers, session,
                                      "publisher")
    if field == FIELD_PUBDATE:
        book.pubdate = _parse_pubdate(value) if value else db.Books.DEFAULT_PUBDATE
        return True
    if field == FIELD_COMMENTS:
        text = clean_string(value, book.id) if value else ""
        if book.comments:
            book.comments[0].text = text
        elif text:
            book.comments.append(db.Comments(comment=text, book=book.id))
        return True
    if field == FIELD_ISBN:
        for identifier in list(book.identifiers):
            if identifier.type.lower() == "isbn":
                session.delete(identifier)
        if value:
            session.add(db.Identifiers(value, "isbn", book.id))
        return True
    if field == FIELD_TAGS:
        tags = [t for t in value.split(",") if t] if value else []
        return modify_database_object(tags, book.tags, db.Tags, session, "tags")
    if field == FIELD_COVER:
        return _write_cover(book, value)
    raise ValueError("Unknown field: {}".format(field))


def _write_cover(book, url):
    from cps import helper
    try:
        ok, error = helper.save_cover_from_url(url, book.path)
    except Exception as ex:  # cover errors must never stop the other fields
        log.warning("Cover download failed for book %s: %s", book.id, ex)
        return False
    if not ok:
        log.warning("Cover download failed for book %s: %s", book.id, error)
        return False
    book.has_cover = 1
    helper.replace_cover_thumbnail_cache(book.id)
    return True


def apply_record(session, book, record, fields=DEFAULT_FIELDS, only_empty=True, recorder=None):
    """Fill the book's fields from one candidate record.

    - only_empty=True (the default): fields that already have a value are left alone.
    - recorder(book_id, field, old_value, new_value): called BEFORE each change, so F7 can
      store the old value first. If it raises, that field is skipped.
    Returns the list of fields that were changed. The caller commits.
    """
    changed = []
    for field in fields:
        new = _new_value(record, field)
        if not new:
            continue
        old = current_value(book, field)
        if only_empty and old:
            continue
        if field != FIELD_COVER and old == new:
            continue
        if recorder is not None:
            try:
                recorder(book.id, field, old, new)
            except Exception as ex:
                log.error("Could not record history for book %s field %s: %s", book.id, field, ex)
                continue
        if _write_field(session, book, field, new):
            changed.append(field)
    if changed:
        book.last_modified = datetime.now(timezone.utc)
    return changed


# ---------------------------------------------------------------------------
# The background task
# ---------------------------------------------------------------------------

class TaskFetchMetadata(CalibreTask):
    """Fetch metadata for the selected books. Shows up on the /tasks page."""

    def __init__(self, book_ids, user_id, threshold=DEFAULT_THRESHOLD, fields=DEFAULT_FIELDS,
                 search=None, recorder_factory=None, task_message=N_("Fetching metadata")):
        super(TaskFetchMetadata, self).__init__(task_message)
        self.log = logger.create()
        self.book_ids = list(dict.fromkeys(int(b) for b in book_ids))  # unique, order kept
        self.user_id = user_id
        self.threshold = threshold
        self.fields = tuple(fields)
        self.search = search or default_search
        # recorder_factory(task_id, user_id) -> recorder(book_id, field, old, new, ...); F7 plugs in here
        self.recorder_factory = recorder_factory
        self.results = []  # one dict per book: book_id, title, result, score, source, fields

    @property
    def name(self):
        return N_("Fetch Metadata")

    @property
    def is_cancellable(self):
        return True

    def _cancelled(self):
        return self.stat in (STAT_ENDED, STAT_CANCELLED)

    def run(self, worker_thread):
        with app.app_context():
            calibre_db = db.CalibreDB(app)
            try:
                items = self._load_items(calibre_db.session)
                total = len(items) or 1
                done = 0
                with concurrent.futures.ThreadPoolExecutor(max_workers=SEARCH_WORKERS) as pool:
                    for start in range(0, len(items), SEARCH_WORKERS):
                        if self._cancelled():
                            break
                        chunk = items[start:start + SEARCH_WORKERS]
                        # searching is slow and needs no DB, so it runs in parallel ...
                        found = list(pool.map(self._safe_search, chunk))
                        # ... but writing to metadata.db stays one book at a time
                        for item, candidates in zip(chunk, found):
                            if self._cancelled():
                                break
                            self.results.append(self._handle_book(calibre_db, item, candidates))
                            done += 1
                            self.progress = done / total
                self.message = self._summary()
                if not self._cancelled():
                    self._handleSuccess()
            except Exception as ex:
                calibre_db.session.rollback()
                self.log.exception(ex)
                self._handleError("Error fetching metadata: {}".format(ex))
            finally:
                calibre_db.session.close()

    def _load_items(self, session):
        """Read what the search needs into plain dicts, so search threads never touch the DB session."""
        items = []
        books = session.query(db.Books).filter(db.Books.id.in_(self.book_ids)).all()
        by_id = {book.id: book for book in books}
        for book_id in self.book_ids:
            book = by_id.get(book_id)
            if book is None:
                self.results.append({"book_id": book_id, "title": "", "result": RESULT_FAILED,
                                     "score": None, "source": "", "fields": [], "error": "Book not found"})
                continue
            items.append({"book_id": book.id, "title": book.title,
                          "authors": [a.name.replace("|", ",") for a in book.authors],
                          "isbn": _clean_isbn(_book_isbn(book))})
        return items

    def _safe_search(self, item):
        try:
            return self.search(item)
        except Exception as ex:
            self.log.warning("Search failed for book %s: %s", item["book_id"], ex)
            return None

    def _handle_book(self, calibre_db, item, candidates):
        entry = {"book_id": item["book_id"], "title": item["title"], "result": RESULT_NO_MATCH,
                 "score": None, "source": "", "fields": []}
        if candidates is None:
            entry["result"] = RESULT_FAILED
            entry["error"] = "Search failed"
            return entry
        if not candidates:
            return entry
        record, score = candidates[0]
        entry["score"] = score
        entry["source"] = record.source.id if record.source else ""
        if score < self.threshold:
            entry["result"] = RESULT_PENDING
            return entry
        book = calibre_db.session.query(db.Books).filter(db.Books.id == item["book_id"]).one_or_none()
        if book is None:
            entry["result"] = RESULT_FAILED
            entry["error"] = "Book not found"
            return entry
        recorder = None
        if self.recorder_factory is not None:
            recorder = self.recorder_factory(str(self.id), self.user_id, source=entry["source"], score=score)
        try:
            changed = apply_record(calibre_db.session, book, record, self.fields, only_empty=True,
                                   recorder=recorder)
            if changed:
                calibre_db.set_metadata_dirty(book.id)
            calibre_db.session.commit()
        except Exception as ex:
            calibre_db.session.rollback()
            self.log.error("Applying metadata to book %s failed: %s", item["book_id"], ex)
            entry["result"] = RESULT_FAILED
            entry["error"] = str(ex)
            return entry
        entry["fields"] = changed
        entry["result"] = RESULT_APPLIED if changed else RESULT_UNCHANGED
        return entry

    def counts(self):
        counts = {RESULT_APPLIED: 0, RESULT_PENDING: 0, RESULT_NO_MATCH: 0, RESULT_UNCHANGED: 0, RESULT_FAILED: 0}
        for entry in self.results:
            counts[entry["result"]] += 1
        return counts

    def _summary(self):
        c = self.counts()
        return "{} applied, {} pending review, {} unchanged, {} no match, {} failed".format(
            c[RESULT_APPLIED], c[RESULT_PENDING], c[RESULT_UNCHANGED], c[RESULT_NO_MATCH], c[RESULT_FAILED])
