#!/usr/bin/env python3
"""Fix the vital-articles URL: "Level 4", not "Level/4".

The probe returned a redirect page's own links --

    {"ns":4,"title":"Wikipedia:Vital articles/Level 4/Technology"}
    {"ns":14,"title":"Category:Redirects from moves"}

-- which is why src_famous found zero ns:0 articles. The page title uses
a SPACE before the number. "Level/4" is an old title kept as a redirect,
and action=parse does not follow redirects unless told to.

Three corrections in one URL:

  Level%2F4  ->  Level%204     the actual page
  plnamespace=0 removed        it belongs to action=query&prop=links,
                               not action=parse; the API answered
                               "Unrecognized parameter: plnamespace"
  redirects=1 added            so either title works if they move the
                               page again

    python3 tools/fix_vital.py
"""
import ast
import pathlib
import sys

OLD = '''VITAL_API = ("https://en.wikipedia.org/w/api.php?action=parse"
             "&page=Wikipedia%3AVital%20articles%2FLevel%2F4%2F{sub}"
             "&prop=links&plnamespace=0&format=json&formatversion=2")
'''

NEW = '''# "Level 4" with a SPACE. "Level/4" exists only as a redirect, and
# action=parse returns the REDIRECT's own links unless redirects=1 is
# set -- four Wikipedia-namespace links and no articles, which read as
# "no vital-articles list". plnamespace is not a parameter of
# action=parse (the API says so in a warning); ns filtering happens in
# _famous_list instead.
VITAL_API = ("https://en.wikipedia.org/w/api.php?action=parse"
             "&page=Wikipedia%3AVital%20articles%2FLevel%204%2F{sub}"
             "&prop=links&redirects=1&format=json&formatversion=2")
'''


def main() -> None:
    p = pathlib.Path("core/pool.py")
    if not p.exists():
        sys.exit("shorts repo only (~/singlescape/isnotwhatyouthink)")
    src = p.read_text()
    if "Level%204" in src:
        print("  core/pool.py: already on the Level 4 URL")
        return
    if src.count(OLD) != 1:
        sys.exit("  core/pool.py: VITAL_API is not there verbatim "
                 "-- run tools/fix_famous.py first. NOT TOUCHED.")
    new = src.replace(OLD, NEW)
    try:
        ast.parse(new)
    except SyntaxError as exc:
        sys.exit(f"  would not parse ({exc}) -- NOT WRITTEN")
    p.write_text(new)
    print("  core/pool.py: VITAL_API fixed (Level 4, redirects followed)")
    print("\n  NOW RUN:  python3 run.py pool")
    print("  You want: famous  ~40 candidates   40 sampled from ~10000 "
          "vital articles")


if __name__ == "__main__":
    main()
