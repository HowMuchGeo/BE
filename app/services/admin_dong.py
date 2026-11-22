#행정동 경계 인덱스
#app/data/admin_dong.json.gz (maintenance/build_admin_dong.py 로 생성) 를 처음 사용할 때 한 번 메모리에 올린다
#출처 : 통계청 SGIS 행정구역 경계(공공누리 제1유형), vuski/admdongkor (CC BY 4.0)
import gzip
import json
import logging
import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from app.utils.geo import Box, Polygon, bbox_of, boxes_overlap, point_in_box, point_in_polygon, polygon_intersects_box

#백엔드 Logger
logger = logging.getLogger(__name__)

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "admin_dong.json.gz"

#공간 인덱스 격자 크기 (도). 0.05도 ≈ 4.4 ~ 5.5km
GRID_DEG = 0.05


@dataclass(frozen=True)
class AdminDong:
    code: str                    #행정동 코드 10자리 (adm_cd2)
    name: str                    #"서울특별시 종로구 사직동"
    polygons: tuple[Polygon, ...]
    bbox: Box
    polygon_bboxes: tuple[Box, ...] = field(repr=False)

    def contains(self, lng: float, lat: float) -> bool:
        if not point_in_box(lng, lat, self.bbox):
            return False
        return any(
            point_in_box(lng, lat, pbox) and point_in_polygon(lng, lat, polygon)
            for polygon, pbox in zip(self.polygons, self.polygon_bboxes)
        )

    def intersects_box(self, box: Box) -> bool:
        if not boxes_overlap(self.bbox, box):
            return False
        return any(
            boxes_overlap(pbox, box) and polygon_intersects_box(polygon, box)
            for polygon, pbox in zip(self.polygons, self.polygon_bboxes)
        )


def _cells(box: Box) -> list[tuple[int, int]]:
    x0, y0 = math.floor(box[0] / GRID_DEG), math.floor(box[1] / GRID_DEG)
    x1, y1 = math.floor(box[2] / GRID_DEG), math.floor(box[3] / GRID_DEG)
    return [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]


class AdminDongIndex:

    def __init__(self, dongs: list[AdminDong], version: str = "") -> None:
        self.dongs = dongs
        self.version = version
        self._grid: dict[tuple[int, int], list[int]] = {}
        for idx, dong in enumerate(dongs):
            for cell in _cells(dong.bbox):
                self._grid.setdefault(cell, []).append(idx)

    def _candidates(self, box: Box) -> list[AdminDong]:
        seen: set[int] = set()
        for cell in _cells(box):
            seen.update(self._grid.get(cell, ()))
        return [self.dongs[idx] for idx in sorted(seen)]

    #BOX 와 겹치는 행정동
    def intersecting(self, box: Box) -> list[AdminDong]:
        return [dong for dong in self._candidates(box) if dong.intersects_box(box)]

    #좌표가 속한 행정동
    def locate(self, lng: float, lat: float) -> AdminDong | None:
        return next((d for d in self._candidates((lng, lat, lng, lat)) if d.contains(lng, lat)), None)

    @classmethod
    def from_payload(cls, payload: dict) -> "AdminDongIndex":
        dongs = []
        for row in payload["dongs"]:
            polygons = tuple(
                tuple(tuple(tuple(point) for point in ring) for ring in polygon)
                for polygon in row["polygons"] if polygon and len(polygon[0]) >= 3
            )
            if not polygons:
                continue
            polygon_bboxes = tuple(bbox_of(polygon[0]) for polygon in polygons)
            bbox = (
                min(b[0] for b in polygon_bboxes), min(b[1] for b in polygon_bboxes),
                max(b[2] for b in polygon_bboxes), max(b[3] for b in polygon_bboxes),
            )
            dongs.append(AdminDong(row["code"], row["name"], polygons, bbox, polygon_bboxes))
        return cls(dongs, payload.get("version", ""))


#전역 인덱스 (최초 호출 시 로드, 약 1초)
@lru_cache(maxsize=1)
def get_index() -> AdminDongIndex:
    with gzip.open(DATA_PATH, "rt", encoding="utf-8") as file:
        index = AdminDongIndex.from_payload(json.load(file))
    logger.warning(f"[ Log ] : 행정동 경계 로드 완료 : {index.version} / {len(index.dongs)} 개")
    return index
