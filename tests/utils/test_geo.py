from app.utils.geo import point_in_polygon, polygon_intersects_box, representative_point

SQUARE = [[(0, 0), (10, 0), (10, 10), (0, 10)]]
WITH_HOLE = [[(0, 0), (10, 0), (10, 10), (0, 10)], [(4, 4), (6, 4), (6, 6), (4, 6)]]


def test_point_in_polygon_with_hole() -> None:
    assert point_in_polygon(2, 2, WITH_HOLE)
    assert not point_in_polygon(5, 5, WITH_HOLE)
    assert not point_in_polygon(11, 5, WITH_HOLE)


def test_polygon_intersects_box() -> None:
    assert polygon_intersects_box(SQUARE, (9, 9, 12, 12))      #모서리 겹침
    assert polygon_intersects_box(SQUARE, (2, 2, 3, 3))        #BOX 가 폴리곤 안
    assert polygon_intersects_box(SQUARE, (-1, -1, 11, 11))    #폴리곤이 BOX 안
    assert polygon_intersects_box(SQUARE, (-1, 4, 11, 6))      #꼭짓점 없이 변만 교차
    assert not polygon_intersects_box(SQUARE, (11, 11, 12, 12))


def test_representative_point_inside_l_shape() -> None:
    l_shape = {"type": "Polygon", "coordinates": [[[0, 0], [10, 0], [10, 1], [1, 1], [1, 10], [0, 10], [0, 0]]]}
    x, y = representative_point(l_shape)
    assert point_in_polygon(x, y, l_shape["coordinates"])
