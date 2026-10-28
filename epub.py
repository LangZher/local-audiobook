"""EPUB → chapters → paragraphs → chunks (the unit of audio). Stdlib only.

An EPUB is either a zip file or (Apple Books, iCloud) a folder with a .epub name; both are read the same way.
Chunk rule (see DESIGN.md): never cross a paragraph; split at sentence ends; merge sentences up to `max_chars`;
a sentence longer than that is split at commas, then cut hard.
"""
import hashlib
import posixpath
import re
import zipfile
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote

NS = {"opf": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/",
      "c": "urn:oasis:names:tc:opendocument:xmlns:container", "ncx": "http://www.daisy.org/z3986/2005/ncx/",
      "x": "http://www.w3.org/1999/xhtml"}


class Epub:
    def __init__(self, path):
        self.path = Path(path)
        self.zip = None if self.path.is_dir() else zipfile.ZipFile(self.path)
        root = ET.fromstring(self.read("META-INF/container.xml"))
        self.opf = root.find(".//c:rootfile", NS).get("full-path")
        self.base = posixpath.dirname(self.opf)
        self.pkg = ET.fromstring(self.read(self.opf))

    def names(self):
        if self.zip:
            return [(i.filename, i.file_size) for i in self.zip.infolist()]
        return [(str(p.relative_to(self.path)), p.stat().st_size) for p in sorted(self.path.rglob("*")) if p.is_file()]

    def read(self, name):
        return self.zip.read(name) if self.zip else (self.path / name).read_bytes()

    def href(self, h):
        return posixpath.normpath(posixpath.join(self.base, unquote(h.split("#")[0])))

    def meta(self, tag):
        e = self.pkg.find(f".//dc:{tag}", NS)
        return (e.text or "").strip() if e is not None else ""

    def manifest(self):
        return {i.get("id"): i for i in self.pkg.findall(".//opf:manifest/opf:item", NS)}

    def book_id(self):
        """Stable id from the contents list, cheap for both zip and folder EPUBs (no full hash)."""
        h = hashlib.sha1((self.meta("title") + "|" + self.meta("creator")).encode())
        for n, s in sorted(self.names()):
            if not n.startswith(("__MACOSX", "iTunes")) and ".DS_Store" not in n:
                h.update(f"{n}:{s};".encode())
        return h.hexdigest()[:12]

    def cover(self):
        """(bytes, media_type) of the cover image, or None."""
        man = self.manifest()
        item = next((i for i in man.values() if "cover-image" in (i.get("properties") or "")), None)
        if item is None:
            m = self.pkg.find(".//opf:meta[@name='cover']", NS)
            item = man.get(m.get("content")) if m is not None else None
        if item is None:
            item = next((i for i in man.values() if "cover" in i.get("id", "").lower()
                         and i.get("media-type", "").startswith("image/")), None)
        if item is None:
            return None
        try:
            return self.read(self.href(item.get("href"))), item.get("media-type")
        except KeyError:
            return None

    def toc(self):
        """href → title from the NCX or the EPUB3 nav, best effort."""
        out, man = {}, self.manifest()
        for i in man.values():
            try:
                if i.get("media-type") == "application/x-dtbncx+xml":
                    for p in ET.fromstring(self.read(self.href(i.get("href")))).iter(f"{{{NS['ncx']}}}navPoint"):
                        t = p.find("ncx:navLabel/ncx:text", NS)
                        c = p.find("ncx:content", NS)
                        if t is not None and c is not None:
                            out.setdefault(posixpath.normpath(posixpath.join(posixpath.dirname(self.href(i.get("href"))),
                                                                             unquote(c.get("src").split("#")[0]))), (t.text or "").strip())
            except (ET.ParseError, KeyError):
                pass
        return out

    def spine(self):
        man = self.manifest()
        for ref in self.pkg.findall(".//opf:spine/opf:itemref", NS):
            item = man.get(ref.get("idref"))
            if item is not None and "html" in item.get("media-type", ""):
                yield self.href(item.get("href"))


BLOCK = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "tr", "section", "dd", "dt", "pre"}


class Paras(HTMLParser):
    """Collect block-level text: [(is_heading, text)]."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.buf, self.skip, self.head = [], [], 0, False

    def flush(self):
        t = re.sub(r"\s+", " ", "".join(self.buf)).strip()
        if t:
            self.out.append((self.head, t))
        self.buf, self.head = [], False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head", "title"):
            self.skip += 1
        elif tag in BLOCK:
            self.flush()
            self.head = tag[0] == "h" and tag[1:].isdigit()
        elif tag == "br":
            self.flush()

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head", "title"):
            self.skip = max(0, self.skip - 1)
        elif tag in BLOCK:
            self.flush()

    def handle_data(self, d):
        if not self.skip:
            self.buf.append(d)


CLOSE = "”’」』\"'）)》"
SENT = re.compile(rf"[^。！？!?；;…]*?(?:[。！？!?；;]+|…+|\.(?=\s|$))[{CLOSE}]*|[^。！？!?；;…]+$")
COMMA = re.compile(rf"[^，,、：:]*[，,、：:][{CLOSE}]*|[^，,、：:]+$")


def split_chunks(text, max_chars=60):
    """Sentences merged greedily up to max_chars; never empty, joined they give back `text`."""
    pieces = []
    for s in (m.group(0) for m in SENT.finditer(text)):
        if len(s) <= max_chars:
            pieces.append(s)
            continue
        for c in (m.group(0) for m in COMMA.finditer(s)):
            while len(c) > max_chars:
                pieces.append(c[:max_chars])
                c = c[max_chars:]
            if c:
                pieces.append(c)
    chunks, cur = [], ""
    for p in pieces:
        if cur and len(cur) + len(p) > max_chars:
            chunks.append(cur)
            cur = ""
        cur += p
    if cur.strip():
        chunks.append(cur)
    return [c for c in chunks if c.strip()] or [text]


def has_words(t):
    return re.search(r"[\w一-鿿]", t) is not None


def parse(path, max_chars=60):
    """The book as JSON-able data: {id, title, author, chapters: [{title, paras: [{h, chunks: [{id, t}]}]}]}."""
    e = Epub(path)
    toc, chapters = e.toc(), []
    for href in e.spine():
        try:
            p = Paras()
            p.feed(e.read(href).decode("utf-8", "replace"))
            p.flush()
        except KeyError:
            continue
        paras = [(h, t) for h, t in p.out if has_words(t)]
        if not paras:
            continue
        ci = len(chapters)
        title = toc.get(href) or next((t for h, t in paras if h), "") or f"§{ci + 1}"
        out, n = [], 0
        for h, t in paras:
            cs = []
            for c in split_chunks(t, max_chars):
                cs.append({"id": f"{ci}-{n}", "t": c})
                n += 1
            out.append({"h": h, "chunks": cs})
        chapters.append({"title": title[:80], "paras": out})
    return {"id": e.book_id(), "title": e.meta("title") or e.path.stem, "author": e.meta("creator"),
            "max_chars": max_chars, "chapters": chapters}


def quick_info(path):
    e = Epub(path)
    return {"id": e.book_id(), "title": e.meta("title") or Path(path).stem, "author": e.meta("creator")}
