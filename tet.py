import os
os.environ["PROXY_USE_ADVANCED_FETCH"] = "true"
os.environ["PROXY_ENABLE_CURL_CFFI"] = "true"

from server import app, sign_proxy_url

sub_playlist_url = "https://leadgenerationblueprint.site/VmxrmarW5/pl/H4sIAAAAAAAAAw3J3XaCIAAA4FcCpE7u0h8wTZYooNwh2DxqxU6ulU._fbefcfiCBgOMhb0xewTtbgj3OBwuAwbosPtQLTf1TR9Nwss6Xj.bhOFK5DELCGYtSepJaqE40OkDGEAiTQn6_7pfosxRzzsU5hUIS5HkJ618yuuVcCWf5TvEReAK13AqMyJ7aKFZImYn8mB0eeqNMJ0K3AczKDY2lTd21dS3ovW0AfJbtvbFsxTbqQJsiuKejqOeuGTKnTQdn1VL_NDyakiZaMR6tJn_dbfRS5nvCnB_6_c6V8gtknpjA3lvkjzughl2AVntPJ7ltdtEoE2Zkh8Hv4BU5HxC3UvJBTqY._rqkd5A.AdhdqFsQQEAAA--/c68f40ed2c538685b01f47a44a7fcc37/index.m3u8"
segment_url = "https://leadgenerationblueprint.site/VmxrmarW5/content/b49e46cefa94b90020bcc4d2e7bf3412/c68f40ed2c538685b01f47a44a7fcc37/page-0.html"

client = app.test_client()

print("1. Fetching sub-playlist:")
path1 = sign_proxy_url(sub_playlist_url, base="")
resp1 = client.get(path1)
print("Status:", resp1.status_code)
print("Content-Type:", resp1.headers.get("Content-Type"))
if resp1.status_code == 200:
    print("Snippet:", resp1.get_data().decode("utf-8")[:300])
else:
    print("Error content:", resp1.get_data().decode("utf-8")[:500])

print("\n2. Fetching segment:")
path2 = sign_proxy_url(segment_url, base="")
resp2 = client.get(path2)
print("Status:", resp2.status_code)
print("Content-Type:", resp2.headers.get("Content-Type"))
if resp2.status_code != 200:
    print("Error content:", resp2.get_data().decode("utf-8")[:500])
