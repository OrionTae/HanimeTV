"""Small end-to-end example for HanimeTV v2. No account needed."""

from HTV import HanimeTV

hentai = HanimeTV()

# SEARCH
print("== search ==")
for video in hentai.search("Example Show", per_page=5):
    print("  %-40s %s" % (video["name"], video["url"]))

# GET INFO (URL, catalog dict or slug all work)
print("\n== info ==")
info = hentai.info("https://hanime.tv/videos/hentai/example-show-1")
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

# BROWSE
print("\n== newest ==")
for video in hentai.search("", order="newest", per_page=5):
    print("  %-40s %s" % (video["name"], video.get("created_at")))
