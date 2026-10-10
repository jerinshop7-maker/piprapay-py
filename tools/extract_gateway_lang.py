"""One-shot extractor: parse lang_text()/instructions()/supported_languages()
from all 48 PHP gateway classes into JSON for the Django port.

PHP string syntax handled: single-quoted with \\' and \\\\ escapes.
Run: python3 tools/extract_gateway_lang.py
"""
import json
import re
import sys
from pathlib import Path

SRC = Path("/app/PipraPay/pp-content/pp-modules/pp-gateways")
OUT = Path("/app/piprapay-django/gateways/lang_data.json")


def extract_block(src: str, func: str) -> str:
    """Extract the [...] returned by `function <func>() { return [...]; }`."""
    m = re.search(r"function\s+" + func + r"\s*\([^)]*\)\s*\{", src)
    if not m:
        return ""
    i = src.index("[", m.end())
    depth = 0
    instr = False
    esc = False
    for j in range(i, len(src)):
        ch = src[j]
        if instr:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == "'":
                instr = False
            continue
        if ch == "'":
            instr = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return src[i:j + 1]
    return ""


def parse_php_string(s: str, pos: int):
    """Parse a single-quoted PHP string starting at pos (s[pos]=="). Returns (value, end)."""
    assert s[pos] == "'"
    out = []
    i = pos + 1
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s) and s[i + 1] in ("'", "\\"):
            out.append(s[i + 1])
            i += 2
            continue
        if ch == "'":
            return "".join(out), i + 1
        out.append(ch)
        i += 1
    raise ValueError("unterminated string")


def parse_php_array(s: str):
    """Parse a PHP [...] literal (strings + nested arrays only). Returns (obj, is_assoc)."""
    pos = 0
    n = len(s)

    def skip_ws(p):
        while p < n and s[p] in " \t\r\n":
            p += 1
        return p

    def parse_value(p):
        p = skip_ws(p)
        if s[p] == "'":
            v, p = parse_php_string(s, p)
            p = skip_ws(p)
            if s.startswith("=>", p):
                p = skip_ws(p + 2)
                vv, p = parse_value(p)
                return ("kv", v, vv), p
            return ("v", v), p
        if s[p] == "[":
            arr, p = parse_array(p)
            p = skip_ws(p)
            return ("v", arr), p
        # numbers / true / false / null
        m = re.match(r"[A-Za-z0-9_.\-]+", s[p:])
        tok = m.group(0)
        return ("v", tok), p + len(tok)

    def parse_array(p):
        assert s[p] == "["
        p = skip_ws(p + 1)
        items = []
        while p < n and s[p] != "]":
            node, p = parse_value(p)
            items.append(node)
            p = skip_ws(p)
            if p < n and s[p] == ",":
                p = skip_ws(p + 1)
        return items, p + 1

    items, _ = parse_array(0)
    # normalize: assoc if all kv
    if items and all(isinstance(x, tuple) and x[0] == "kv" for x in items):
        return {x[1]: (norm(x[2])) for x in items}
    return [norm(x[1] if x[0] == "v" else x) for x in items]


def norm(v):
    if isinstance(v, tuple):
        if v[0] == "v":
            return norm(v[1])
        return norm(v[2]) if len(v) > 2 else v
    if isinstance(v, list):
        if v and all(isinstance(x, tuple) and x[0] == "kv" for x in v):
            return {x[1]: norm(x[2]) for x in v}
        return [norm(x) for x in v]
    return v


def strip_php_comments(s: str) -> str:
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.DOTALL)
    s = re.sub(r"//[^\n]*", "", s)
    return s


def main():
    result = {}
    for d in sorted(SRC.iterdir()):
        cls = d / "class.php"
        if not cls.exists():
            continue
        src = cls.read_text(encoding="utf-8", errors="replace")
        slug = d.name
        entry: dict = {}
        for func in ("supported_languages", "lang_text"):
            block = extract_block(src, func)
            if block:
                try:
                    entry[func] = parse_php_array(strip_php_comments(block))
                except Exception as e:
                    print(f"WARN {slug} {func}: {e}", file=sys.stderr)
        # instructions: structural summary per step (text key, copy, value source, action)
        m = re.search(r"function\s+instructions\s*\([^)]*\)\s*\{", src)
        steps = []
        if m:
            body = src[m.end():m.end() + 12000]
            # one chunk per step: text between consecutive 'text' keys
            text_hits = list(re.finditer(r"'text'\s*=>\s*'([^']+)'", body))
            for idx, sm in enumerate(text_hits):
                end = text_hits[idx + 1].start() if idx + 1 < len(text_hits) else len(body)
                chunk = body[sm.start():end]
                step = {"text": sm.group(1)}
                cm = re.search(r"'copy'\s*=>\s*(true|false)", chunk)
                if cm:
                    step["copy"] = cm.group(1) == "true"
                vm = re.search(r"'value'\s*=>\s*\$data\['(\w+)'\]\['(\w+)'\]", chunk)
                if vm:
                    step["value"] = [vm.group(1), vm.group(2)]
                am = re.search(r"'action'\s*=>\s*\[.*?'type'\s*=>\s*'(\w+)'", chunk, re.DOTALL)
                if am:
                    step["action"] = am.group(1)
                    av = re.search(r"'value'\s*=>\s*\$data\['(\w+)'\]\['(\w+)'\]\s*\?\?\s*''", chunk)
                    if av:
                        step["action_value"] = [av.group(1), av.group(2)]
                vr = re.search(r"'vars'\s*=>\s*\[(.*?)\]", chunk, re.DOTALL)
                if vr:
                    step["vars"] = re.findall(r"'\{(\w+)\}'", vr.group(1))
                steps.append(step)
                if len(steps) > 12:
                    break
        entry["instructions"] = steps
        result[slug] = entry

    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1))
    n_lang = sum(1 for v in result.values() if v.get("lang_text"))
    n_ins = sum(1 for v in result.values() if v.get("instructions"))
    print(f"gateways={len(result)} with_lang={n_lang} with_instructions={n_ins} -> {OUT}")


if __name__ == "__main__":
    main()
