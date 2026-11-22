#행정동 경계 데이터 경량화 스크립트
#원본 : https://github.com/vuski/admdongkor (ver*/HangJeongDong_ver*.geojson)
#출처 : 통계청 SGIS 행정구역 경계 (공공누리 제1유형) · vuski/admdongkor (CC BY 4.0)
#
#사용법 : python maintenance/build_admin_dong.py HangJeongDong_ver20261001.geojson
#결과   : app/data/admin_dong.json.gz
#  {"version": "...", "source": "...", "dongs": [{"code": adm_cd2, "name": adm_nm, "polygons": [[ring, ...], ...]}]}
#  좌표는 소수 6자리(약 0.1m)로 반올림
import gzip
import json
import sys
from pathlib import Path

OUTPUT = Path(__file__).resolve().parent.parent / "app" / "data" / "admin_dong.json.gz"
SOURCE = "통계청 SGIS 행정구역 경계(공공누리 제1유형), vuski/admdongkor (CC BY 4.0)"


def _ring(ring: list) -> list:
    out = []
    for lng, lat, *_ in ring:
        point = [round(lng, 6), round(lat, 6)]
        if not out or out[-1] != point:
            out.append(point)
    return out


def main(path: str) -> None:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    dongs = []
    for feature in raw["features"]:
        props, geometry = feature["properties"], feature["geometry"]
        polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
        dongs.append({
            "code": props["adm_cd2"],
            "name": props["adm_nm"],
            "polygons": [[_ring(ring) for ring in polygon] for polygon in polygons],
        })

    payload = {"version": raw.get("name", Path(path).stem), "source": SOURCE, "dongs": dongs}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUTPUT, "wt", encoding="utf-8", compresslevel=9) as file:
        json.dump(payload, file, ensure_ascii=False, separators=(",", ":"))
    print(f"{len(dongs)} 개 행정동 → {OUTPUT} ({OUTPUT.stat().st_size / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    main(sys.argv[1])
