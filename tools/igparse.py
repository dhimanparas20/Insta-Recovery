"""Minimal stdlib parser for Instagram 'Download your information' HTML exports.

Builds a tiny DOM with html.parser and exposes helpers to walk the
top-level entries (div.pam ... _a6-g) inside <main>.
"""
from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from pathlib import Path

VOID = {"br", "img", "meta", "link", "input", "hr", "base", "source", "wbr", "col", "area", "embed", "param", "track"}


class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag, attrs=None, parent=None):
        self.tag = tag
        self.attrs = dict(attrs or {})
        self.children = []
        self.parent = parent

    # --- queries -----------------------------------------------------
    @property
    def classes(self):
        return set((self.attrs.get("class") or "").split())

    def iter(self):
        for c in self.children:
            if isinstance(c, Node):
                yield c
                yield from c.iter()

    def find_all(self, tag=None, cls=None):
        for n in self.iter():
            if tag and n.tag != tag:
                continue
            if cls and cls not in n.classes:
                continue
            yield n

    def find(self, tag=None, cls=None):
        return next(self.find_all(tag, cls), None)

    def kids(self, tag=None, cls=None):
        for c in self.children:
            if isinstance(c, Node) and (not tag or c.tag == tag) and (not cls or cls in c.classes):
                yield c

    def text(self, sep=""):
        out = []
        for c in self.children:
            if isinstance(c, Node):
                if c.tag == "br":
                    out.append("\n")
                elif c.tag in ("style", "script"):
                    continue
                else:
                    out.append(c.text(sep))
            else:
                out.append(c)
        return sep.join(out)

    def own_text(self):
        """Text of direct string children only."""
        return "".join(c for c in self.children if isinstance(c, str)).strip()


class _Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        n = Node(tag, attrs, self.cur)
        self.cur.children.append(n)
        if tag not in VOID:
            self.cur = n

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(Node(tag, attrs, self.cur))

    def handle_endtag(self, tag):
        n = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:
            self.cur = n.parent

    def handle_data(self, data):
        self.cur.children.append(data)


def load(path) -> Node:
    b = _Builder()
    b.feed(Path(path).read_text(encoding="utf-8"))
    return b.root


def main_node(root: Node) -> Node:
    return root.find("main") or root


def entries(root: Node):
    """Top-level entry blocks inside <main>."""
    return list(main_node(root).kids("div", "_a6-g"))


def entry_title(e: Node):
    h = next(e.kids("h2"), None)
    return h.text().strip() if h else None


def entry_time(e: Node):
    t = next(e.kids("div", "_a6-o"), None)
    return t.text().strip() if t else None


def links(n: Node):
    return [a.attrs.get("href") for a in n.find_all("a") if a.attrs.get("href")]


def media_srcs(n: Node):
    out = []
    for m in n.find_all():
        if m.tag in ("img", "video") and m.attrs.get("src"):
            out.append(m.attrs["src"])
    # de-dupe preserving order
    seen, res = set(), []
    for s in out:
        if s not in seen:
            seen.add(s)
            res.append(s)
    return res


def table_fields(n: Node):
    """Collect label->value pairs from export tables.

    Two layouts exist:
      <td class=_a6_q>Label</td><td class=_a6_r>Value</td>
      <td colspan=2 class=_a6_q>Label<div>...value...</div></td>
    Only the first occurrence of each label (shallowest) is kept.
    """
    fields = {}
    for tr in n.find_all("tr"):
        tds = list(tr.kids("td"))
        if len(tds) == 2:
            k, v = tds[0].text().strip(), tds[1].text().strip()
        elif len(tds) == 1:
            k = tds[0].own_text()
            v = "".join(c.text() for c in tds[0].kids()).strip()
        else:
            continue
        if k and k not in fields:
            fields[k] = v
    return fields


def clean(s):
    return html.unescape(re.sub(r"[ \t]+", " ", s or "")).strip()
