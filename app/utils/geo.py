#평면 좌표(경도, 위도) 기하 연산 유틸
#수백 m ~ 수 km 범위의 포함·교차 판정만 하므로 위경도를 평면으로 보고 계산한다
from collections.abc import Iterable, Sequence

Point = Sequence[float]          #(lng, lat)
Ring = Sequence[Point]           #닫힌 고리. 첫 점 = 끝 점 여부는 상관없다
Polygon = Sequence[Ring]         #[외곽, 구멍, 구멍, ...]
Box = tuple[float, float, float, float]  #(min_lng, min_lat, max_lng, max_lat)


#=== BOX ===#
def bbox_of(points: Iterable[Point]) -> Box:
    lngs, lats = zip(*((p[0], p[1]) for p in points))
    return min(lngs), min(lats), max(lngs), max(lats)


def boxes_overlap(a: Box, b: Box) -> bool:
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def point_in_box(lng: float, lat: float, box: Box) -> bool:
    return box[0] <= lng <= box[2] and box[1] <= lat <= box[3]


def box_corners(box: Box) -> list[tuple[float, float]]:
    return [(box[0], box[1]), (box[2], box[1]), (box[2], box[3]), (box[0], box[3])]


#=== 폴리곤 ===#
#짝홀 규칙 point-in-polygon. 구멍(내부 고리)도 같은 규칙으로 처리된다
def point_in_polygon(lng: float, lat: float, polygon: Polygon) -> bool:
    inside = False
    for ring in polygon:
        n = len(ring)
        j = n - 1
        for i in range(n):
            xi, yi = ring[i][0], ring[i][1]
            xj, yj = ring[j][0], ring[j][1]
            if (yi > lat) != (yj > lat) and lng < (xj - xi) * (lat - yi) / (yj - yi) + xi:
                inside = not inside
            j = i
    return inside


def _cross(o: Point, a: Point, b: Point) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _on_segment(p: Point, q: Point, r: Point) -> bool:
    return min(p[0], r[0]) <= q[0] <= max(p[0], r[0]) and min(p[1], r[1]) <= q[1] <= max(p[1], r[1])


def segments_intersect(p1: Point, p2: Point, q1: Point, q2: Point) -> bool:
    d1, d2 = _cross(q1, q2, p1), _cross(q1, q2, p2)
    d3, d4 = _cross(p1, p2, q1), _cross(p1, p2, q2)
    if ((d1 > 0) != (d2 > 0)) and d1 != 0 and d2 != 0 and ((d3 > 0) != (d4 > 0)) and d3 != 0 and d4 != 0:
        return True
    return (
        (d1 == 0 and _on_segment(q1, p1, q2)) or (d2 == 0 and _on_segment(q1, p2, q2))
        or (d3 == 0 and _on_segment(p1, q1, p2)) or (d4 == 0 and _on_segment(p1, q2, p2))
    )


#폴리곤과 BOX 가 겹치는지 (경계 접촉 포함)
def polygon_intersects_box(polygon: Polygon, box: Box) -> bool:
    outer = polygon[0]
    #1) 폴리곤 꼭짓점이 BOX 안
    if any(point_in_box(p[0], p[1], box) for p in outer):
        return True
    #2) BOX 꼭짓점이 폴리곤 안 (BOX 가 폴리곤 내부에 통째로 들어간 경우)
    corners = box_corners(box)
    if any(point_in_polygon(x, y, polygon) for x, y in corners):
        return True
    #3) 변끼리 교차
    edges = list(zip(corners, corners[1:] + corners[:1]))
    for ring in polygon:
        for i in range(len(ring)):
            a, b = ring[i - 1], ring[i]
            if not boxes_overlap((min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])), box):
                continue
            if any(segments_intersect(a, b, c, d) for c, d in edges):
                return True
    return False


#=== GeoJSON ===#
#Polygon / MultiPolygon geometry → 폴리곤 목록
def geometry_polygons(geometry: dict | None) -> list[Polygon]:
    if not geometry:
        return []
    kind, coords = geometry.get("type"), geometry.get("coordinates") or []
    if kind == "Polygon":
        return [coords]
    if kind == "MultiPolygon":
        return list(coords)
    return []


#고리의 면적 중심. 면적이 0 이면 꼭짓점 평균
def ring_centroid(ring: Ring) -> tuple[float, float]:
    area = cx = cy = 0.0
    for i in range(len(ring)):
        x0, y0 = ring[i - 1][0], ring[i - 1][1]
        x1, y1 = ring[i][0], ring[i][1]
        cross = x0 * y1 - x1 * y0
        area += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    if area == 0:
        n = len(ring)
        return sum(p[0] for p in ring) / n, sum(p[1] for p in ring) / n
    return cx / (3 * area), cy / (3 * area)


#필지 대표점 : 가장 큰 외곽 고리의 면적 중심. ㄱ자 필지처럼 중심이 밖에 떨어지면 외곽 꼭짓점 중 하나를 쓴다
def representative_point(geometry: dict | None) -> tuple[float, float] | None:
    polygons = [p for p in geometry_polygons(geometry) if p and len(p[0]) >= 3]
    if not polygons:
        return None

    def ring_area(ring: Ring) -> float:
        return abs(sum(ring[i - 1][0] * ring[i][1] - ring[i][0] * ring[i - 1][1] for i in range(len(ring))))

    polygon = max(polygons, key=lambda p: ring_area(p[0]))
    lng, lat = ring_centroid(polygon[0])
    if point_in_polygon(lng, lat, polygon):
        return lng, lat

    #외곽 꼭짓점과 면적 중심의 중점 중 폴리곤 안에 들어가는 첫 점
    for x, y in polygon[0]:
        mx, my = (x + lng) / 2, (y + lat) / 2
        if point_in_polygon(mx, my, polygon):
            return mx, my
    return polygon[0][0][0], polygon[0][0][1]


def geometry_bbox(geometry: dict | None) -> Box | None:
    points = [p for polygon in geometry_polygons(geometry) for p in (polygon[0] if polygon else [])]
    return bbox_of(points) if points else None
