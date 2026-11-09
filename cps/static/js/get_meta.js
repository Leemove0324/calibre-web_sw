/* This file is part of the Calibre-Web (https://github.com/janeczku/calibre-web)
 *    Copyright (C) 2018  idalin<dalin.lin@gmail.com>
 *
 *  This program is free software: you can redistribute it and/or modify
 *  it under the terms of the GNU General Public License as published by
 *  the Free Software Foundation, either version 3 of the License, or
 *  (at your option) any later version.
 *
 *  This program is distributed in the hope that it will be useful,
 *  but WITHOUT ANY WARRANTY; without even the implied warranty of
 *  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 *  GNU General Public License for more details.
 *
 *  You should have received a copy of the GNU General Public License
 *  along with this program. If not, see <http://www.gnu.org/licenses/>.
 */
/* global _, i18nMsg, tinymce, getPath */

$(function () {
    var msg = i18nMsg;
    var keyword = ""

    var templates = {
        bookResult: _.template(
            $("#template-book-result").html()
       )
    };

    function isbnInput() {
        return $("#identifier-table input[name^='identifier-type-']").filter(function () {
            return $(this).val().trim().toLowerCase() === "isbn";
        }).first().closest("tr").find("input[name^='identifier-val-']");
    }

    function textValue(value) {
        return typeof value === "string" || typeof value === "number" ? String(value) : "";
    }

    function editor() {
        return typeof tinymce !== "undefined" ? tinymce.get("comments") : null;
    }

    // Parse in an inert template, then render only text in the comparison table.
    // The original HTML remains unchanged when the user applies the description.
    function descriptionText(html) {
        var template = document.createElement("template");
        template.innerHTML = html;
        var excluded = /^(SCRIPT|STYLE|IFRAME|OBJECT|EMBED|NOSCRIPT|TEMPLATE)$/;
        var blocks = /^(P|DIV|LI|UL|OL|H[1-6]|BLOCKQUOTE|PRE|TR|SECTION|ARTICLE)$/;
        function read(node) {
            if (node.nodeType === 3) return node.nodeValue;
            if (excluded.test(node.nodeName)) return "";
            if (node.nodeName === "BR") return "\n";
            var text = Array.prototype.map.call(node.childNodes, read).join("");
            return blocks.test(node.nodeName) ? "\n" + text + "\n" : text;
        }
        return read(template.content).replace(/\n[ \t]+/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
    }

    function coverPreview(cell, source, label) {
        var fallback = $("<span class='text-muted'>").text(msg.cover_unavailable).appendTo(cell);
        if (!source) return;
        // Only web images are previewed; local relative cover routes are supported.
        var url;
        try { url = new URL(source, window.location.href); } catch (error) { return; }
        if (url.protocol !== "http:" && url.protocol !== "https:") return;
        var image = $("<img>").attr({alt: label, referrerpolicy: "no-referrer"})
            .css({width: "100%", height: "150px", "max-width": "110px", "object-fit": "contain"})
            .hide().appendTo(cell);
        image.on("load", function () { fallback.hide(); image.show(); });
        image.on("error", function () { image.hide(); fallback.show(); });
        image.attr("src", url.href);
    }

    function populateForm(book) {
        var isbn = "";
        Object.keys(book.identifiers || {}).forEach(function (key) {
            if (key.toLowerCase() === "isbn") isbn = textValue(book.identifiers[key]);
        });
        var date = textValue(book.publishedDate);
        var parsed = new Date(date);
        if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || isNaN(parsed.getTime()) ||
                parsed.toISOString().slice(0, 10) !== date) date = "";
        var cover = textValue(book.cover);
        if (!/^https?:\/\//i.test(cover)) cover = "";
        var canApplyCover = $("#cover_url").length > 0;
        var fields = [
            ["title", msg.title, textValue(book.title)],
            ["authors", msg.author, (book.authors || []).map(textValue).join(" & ")],
            ["publisher", msg.publisher, textValue(book.publisher)],
            ["pubdate", msg.pubdate, date],
            ["isbn", "ISBN", isbn],
            ["cover_url", msg.cover, cover],
            ["comments", msg.comments, textValue(book.description)]
        ];
        var panel = $("#metadata-compare").empty().show();
        $("<h4>").text(msg.compare).appendTo(panel);
        $("<p>").text(msg.apply_hint).appendTo(panel);
        var table = $("<table class='table table-bordered'>").appendTo(
            $("<div class='table-responsive'>").appendTo(panel));
        var head = $("<tr>").appendTo($("<thead>").appendTo(table));
        [msg.field, msg.current, msg.candidate].forEach(function (label) {
            $("<th scope='col'>").text(label).appendTo(head);
        });
        var body = $("<tbody>").appendTo(table);
        var selected = [];
        var apply = $("<button type='button' class='btn btn-primary'>").text(msg.apply).prop("disabled", true);
        fields.forEach(function (field) {
            var id = field[0], value = field[2];
            var current = id === "isbn" ? isbnInput().val() :
                id === "comments" && editor() ? editor().getContent() : $("#" + id).val();
            if (id === "cover_url" && !current) current = $(".cover img").attr("src");
            var row = $("<tr>").appendTo(body);
            var label = $("<label>").appendTo($("<td>").appendTo(row));
            var check = $("<input type='checkbox'>").prop("checked", false)
                .prop("disabled", !value.trim() || (id === "cover_url" && !canApplyCover)).appendTo(label);
            label.append(document.createTextNode(" " + field[1]));
            if (id === "cover_url" && !canApplyCover) {
                $("<p class='text-muted'>").text(msg.cover_permission).appendTo(label.parent());
            }
            [current || "", value].forEach(function (content, index) {
                var cell = $("<td>").css({"white-space": "pre-wrap", "overflow-wrap": "anywhere", "max-width": "300px"})
                    .appendTo(row);
                if (id === "cover_url") {
                    coverPreview(cell, content, index === 0 ? msg.current : msg.candidate);
                } else {
                    cell.text((id === "comments" ? descriptionText(content) : content) ||
                        (index === 0 ? msg.empty_value : msg.unavailable));
                }
            });
            selected.push({check: check, id: id, value: value});
            check.on("change", function () {
                apply.prop("disabled", !selected.some(function (item) { return item.check.prop("checked"); }));
            });
        });
        apply.appendTo(panel).on("click", function () {
            selected.forEach(function (item) {
                if (item.check.prop("disabled") || !item.check.prop("checked")) return;
                if (item.id === "isbn") {
                    var input = isbnInput();
                    if (!input.length) {
                        var row = $("<tr>").appendTo($("#identifier-table tbody"));
                        var suffix = "f5-" + Date.now();
                        $("<input type='text' class='form-control' required>").attr("name", "identifier-type-" + suffix)
                            .val("isbn").appendTo($("<td>").appendTo(row));
                        input = $("<input type='text' class='form-control' required>").attr("name", "identifier-val-" + suffix)
                            .appendTo($("<td>").appendTo(row));
                        $("<button type='button' class='btn btn-default'>").text(msg.remove)
                            .on("click", function () { row.remove(); }).appendTo($("<td>").appendTo(row));
                    }
                    input.val(item.value);
                } else if (item.id === "comments" && editor()) {
                    editor().setContent(item.value);
                } else {
                    $("#" + item.id).val(item.value).trigger("change");
                    if (item.id === "pubdate" && $.fn.datepicker) $("#pubdate").datepicker("update", item.value);
                    if (item.id === "cover_url") $(".cover img").attr("src", item.value);
                }
            });
            $("#metaModal").modal("hide");
        });
        $("<button type='button' class='btn btn-default'>").text(msg.cancel).appendTo(panel)
            .on("click", function () { panel.empty().hide(); });
    }

    function doSearch (keyword) {
        $("#metadata-compare").empty().hide();
        if (keyword) {
            $("#meta-info").text(msg.loading);
            $.ajax({
                url: getPath() + "/metadata/search",
                type: "POST",
                data: {"query": keyword},
                dataType: "json",
                success: function success(data) {
                    if (data.length) {
                        $("#meta-info").html("<ul id=\"book-list\" class=\"media-list\"></ul>");
                        data.forEach(function(book) {
                            var $book = $(templates.bookResult(book));
                            $book.find("img, .metadata-compare-button").on("click", function () {
                                populateForm(book);
                            });
                            $("#book-list").append($book);
                        });
                    }
                    else {
                        $("#meta-info").empty().append($("<p class='text-danger'>").text(msg.no_result));
                    }
                },
                error: function error() {
                    $("#meta-info").empty().append($("<p class='text-danger'>").text(msg.search_error));
                },
            });
        }
    }

    function populate_provider() {
        $("#metadata_provider").empty();
        $.ajax({
            url: getPath() + "/metadata/provider",
            type: "get",
            dataType: "json",
            success: function success(data) {
                data.forEach(function(provider) {
                    var checked = "";
                    if (provider.active) {
                        checked = "checked";
                    }
                    var $provider_button = '<input type="checkbox" id="show-' + provider.name + '" class="pill" data-initial="' + provider.initial + '" data-control="' + provider.id + '" ' + checked + '><label for="show-' + provider.name + '">' + provider.name + ' <span class="glyphicon glyphicon-ok"></span></label>'
                    $("#metadata_provider").append($provider_button);
                });
            },
        });
    }

    $(document).on("change", ".pill", function () {
        var element = $(this);
        var id = element.data("control");
        var initial = String(element.data("initial")) === "true";
        var val = element.prop('checked');
        var params = {id : id, value: val};
        $('[data-related="' + id + '"]').toggle(val);
        if (!initial) {
            params['initial'] = initial;
            params['query'] = keyword;
        }
        $.ajax({
            method:"post",
            contentType: "application/json; charset=utf-8",
            dataType: "json",
            url: getPath() + "/metadata/provider/" + id,
            data: JSON.stringify(params),
            success: function success(data) {
                element.data("initial", "true");
                if (!Array.isArray(data)) return;
                if (data.length && !$("#book-list").length) {
                    $("#meta-info").empty().append($("<ul id='book-list' class='media-list'>"));
                }
                data.forEach(function(book) {
                    var $book = $(templates.bookResult(book));
                    $book.find("img, .metadata-compare-button").on("click", function () {
                        populateForm(book);
                    });
                    $("#book-list").append($book);
                });
            }
        });
    });

    $("#meta-search").on("submit", function (e) {
        e.preventDefault();
        keyword = $("#keyword").val();
        $('.pill').each(function(){
            $(this).data("initial", $(this).prop('checked'));
        });
        doSearch(keyword);
    });

    $("#get_meta").click(function () {
        populate_provider();
        var bookTitle = $("#title").val();
        $("#keyword").val(bookTitle);
        keyword = bookTitle;
        doSearch(bookTitle);
    });
    $("#metaModal").on("hidden.bs.modal", function () {
        $("#metadata-compare").empty().hide();
    });
    $("#metaModal").on("show.bs.modal", function(e) {
        $(e.relatedTarget).one('focus', function (e) {
            $(this).blur();
        });
    });
});
