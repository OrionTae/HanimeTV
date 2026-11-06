"""Small end-to-end example for HanimeTV v2. No account needed."""

from HTV import HanimeTV

hentai = HanimeTV()

# BROWSE - an empty query lists the catalog, so no title has to be hardcoded
print("== most viewed ==")
videos = hentai.search("", order="views", per_page=5)
for video in videos:
    print("  %-40s %s" % (video["name"], video["url"]))

if not videos:
    raise SystemExit("catalog returned nothing - is the site reachable?")

# GET INFO - accepts a URL, a catalog dict, or a slug
print("\n== info ==")
info = hentai.info(videos[0])
print("  name   :", info["name"])
print("  brand  :", info.get("brand"))
print("  views  :", info.get("views"))
print("  tags   :", ", ".join(info.get("tags", [])[:6]))
print("  poster :", info.get("poster_url"))

# GET STORYBOARDS
print("\n== storyboards ==")
storyboards = hentai.storyboards(info)
print("  sheet  :", storyboards["url"])
print("  grid   : %sx%s (%s tiles)" % (
    storyboards["columns"], storyboards["rows"], storyboards["tile_count"]))

# DOWNLOADS - no account needed for up to 720p
#   python -m HTV download <slug> -q 720
# or from Python:
#   hentai.download("<slug>", quality="720")
print("\nFor MP4 downloads: `python -m HTV download <slug> -q 720`")
