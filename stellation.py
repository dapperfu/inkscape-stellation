#! /usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import division
'''
Stellation extension for Inkscape.

'''

import inkex       # Required
import simplestyle # will be needed here for styles support
import simpletransform
import cubicsuperpath
import cspsubdiv
import os          # here for alternative debug method only - so not usually required
import json
# many other useful ones in extensions folder. E.g. simplepath, cubicsuperpath, ...

import math
from math import cos, sin, radians, sqrt, pi
phi = (1+sqrt(5))/2
EPSILON = .00001 # arbitrary small #
FLATNESS = 0.25 # minimum flatness of subdivided curves
SCOOTCH = '.001mm' # "scootch" amount

__version__ = '0.0.0'

inkex.localize()


### Your helper functions go here
def points_to_svgd(p, close=True):
    """ convert list of points (x,y) pairs
        into a closed SVG path list
    """
    f = p[0]
    p = p[1:]
    svgd = 'M%.4f,%.4f' % f
    for x in p:
        svgd += 'L%.4f,%.4f' % x
    if close:
        svgd += 'z'
    return svgd

# if a -> b -> c a counter-clockwise turn?
# +1 if counter-clockwise, -1 is clockwise, 0 if colinear
# XXX 2D ONLY XXX
def ccw(a, b, c):
    area2 = (b.x - a.x) * (c.y - a.y) - (c.x - a.x) * (b.y - a.y);
    if area2 < 0:
        return -1
    elif area2 > 0:
        return +1
    else:
        return 0

# Valid on 3d points
def ccw_from_origin(a, b, c, V = None):
    N = normal(a, b, c)
    w = N.dot(a if V is None else (a - V))
    if w < 0:
        return -1
    elif w > 0:
        return +1
    else:
        return 0

def normal(a, b, c):
    return (b - a).cross(c - a)

def near_zero(x):
    return (abs(x) < EPSILON)

def safe_sqrt(x):
    if x < 0 and near_zero(x): return 0
    return sqrt(x)

class Point:
    """Points are three dimensional."""
    x = 0
    y = 0
    z = 0
    def __init__(self, x, y, z):
        self.x = x
        self.y = y
        self.z = z
    def __repr__(self):
        return "Point(%r,%r,%r)" % (self.x, self.y, self.z)
    def __str__(self):
        return "(%f,%f,%f)" % (self.x, self.y, self.z)
    def __add__(self, other):
        return Point(self.x + other.x, self.y + other.y, self.z + other.z)
    def __sub__(self, other):
        return Point(self.x - other.x, self.y - other.y, self.z - other.z)
    def __mul__(self, other):
        return Point(self.x * other, self.y * other, self.z * other)
    def __truediv__(self, other):
        return Point(self.x / other, self.y / other, self.z / other)
    def __neg__(self):
        return Point(-self.x, -self.y, -self.z)
    def __iadd__(self, other):
        self.x += other.x
        self.y += other.y
        self.z += other.z
        return self
    def __itruediv__(self, other):
        self.x /= other
        self.y /= other
        self.z /= other
        return self
    def dist(self, other=None):
        return safe_sqrt(self.dist2(other))
    def dist2(self, other=None):
        pt = self if other is None else (self - other)
        return pt.dot(pt)
    def cross(self, other):
        return Point(
            self.y * other.z - self.z * other.y,
            -(self.x * other.z - self.z * other.x),
            self.x * other.y - self.y * other.x
        )
    def dot(self, other):
        return (self.x * other.x) + (self.y * other.y) + (self.z * other.z)
    def normalized(self):
        return self / self.dist()
    def transform(self, matrix):
        return matrix.transform(self)

class Vector(Point):
    pass

class Face:
    """A face is a planar collection of points"""
    points = None
    inside = None
    def __init__(self, *points, **kwargs):
        self.points = points
        self.inside = kwargs.get('inside')
        # check that points wind CW when viewed from origin (inside)
        l = len(points)
        for i in xrange(0, l):
            assert ccw_from_origin(points[i], points[(i+1)%l], points[(i+2)%l], V=self.inside) > 0
    def __repr__(self):
        return "Face(" + (",".join(repr(pt) for pt in self.points)) + \
            ("" if self.inside is None else (", inside=%r" % self.inside)) + \
            ")"
    def __str__(self):
        return "Face(" + (",".join(str(pt) for pt in self.points)) + ")"
    def __mul__(self, other):
        return Face(*[pt*other for pt in self.points], inside=self.inside)
    def __truediv__(self, other):
        return Face(*[pt/other for pt in self.points], inside=self.inside)
    def shuffle(self, amt=1):
        pts = list(self.points)
        pts = pts[amt:] + pts[:amt]
        return Face(*pts, inside=self.inside)
    def centroid(self):
        sum = Point(0,0,0)
        count = 0
        for pt in self.points:
            sum += pt
            count += 1
        return sum / count
    def plane(self):
        return Plane(
            self.centroid(),
            normal(self.points[0], self.points[1], self.points[2])
        )
    def transform(self, matrix):
        return Face(*[p.transform(matrix) for p in self.points], **{
            'inside': None if self.inside is None else \
                self.inside.transform(matrix)
        })

class Line:
    """A line is represented as a point and a direction vector."""
    point = None
    direction = None
    def __init__(self, point, direction):
        self.point = point
        self.direction = direction.normalized()
    def __repr__(self):
        return "Line(%r,%r)" % (self.point, self.direction)
    def intersect(self, other):
        if isinstance(other, Plane):
            return other.intersectLine(self)
        assert False
    def intersect2dSegment(self, p0, p1):
        """Special 2d intersection (ignore z) w/ segment"""
        def perp(u, v): return u.x*v.y - u.y*v.x
        u = self.direction
        v = p1 - p0
        w = self.point - p0
        D = perp(u,v)
        # test if they are parallel
        if near_zero(D): # self and p0->p1 are parallel
            if (not near_zero(perp(u,w))) or (not near_zero(perp(v,w))):
                return [] # parallel but NOT collinear
            # collinear or degenerate
            if near_zero(u.x):
                s0 = (p0.y - self.point.y) / u.y
                s1 = (p1.y - self.point.y) / u.y
            else:
                s0 = (p0.x - self.point.x) / u.x
                s1 = (p1.x - self.point.x) / u.x
            return [s0, s1]
        # the segments are skew and may intersect in a point
        sI = perp(v,w) / D
        tI = perp(u,w) / D
        if tI < 0 or tI > 1:
            return [] # no intersection with p0->p1
        return [sI] # intersect point

    def transform(self, matrix):
        p1 = self.point.transform(matrix)
        p2 = (self.point + self.direction).transform(matrix)
        return Line(p1, p2 - p1)

class Plane:
    """A plane is represented as a point (usually the center point of a face)
    and a normal vector."""
    point = None
    normal = None
    def __init__(self, point, normal):
        # Passes through this point
        self.point = point
        # Unit normal vector; also the direction cosines of the angle n
        # makes with the xyz-axes
        self.normal = normal.normalized()
    def __repr__(self):
        return "Plane(%r,%r)" % (self.point, self.normal)
    def d(self):
        """Perpendicular distance from the origin to the plane."""
        return -(self.normal.dot(self.point))
    def arbitrary_point(self):
        # nonzero vector parallel to the plane
        u = Vector(1,0,0) if near_zero(self.normal.x) and near_zero(self.normal.y) \
            else Vector(-self.normal.y, self.normal.x, 0)
        return self.point + u
    def transform(self, matrix):
        p1 = self.point.transform(matrix)
        p2 = (self.point + self.normal).transform(matrix)
        return Plane(p1, p2 - p1)
    def intersect(self, other):
        if isinstance(other, Plane):
            return self.intersectPlane(other)
        if isinstance(other, Line):
            return self.intersectLine(other)
        assert False
    def intersectLine(self, line):
        nu = self.normal.dot(line.direction)
        if near_zero(nu):
            return None # parallel
        s = self.normal.dot(self.point - line.point) / nu
        return line.point + (line.direction * s)
    def intersectPlane(self, plane):
        n3 = self.normal.cross(plane.normal)
        if near_zero(n3.dist()):
            return None
        # Now find a point on the line
        n1 = self.normal
        d1 = self.d()
        n2 = plane.normal
        d2 = plane.d()
        P0 = (n1*d2 - n2*d1).cross(n3) / n3.dist2()
        return Line(P0, n3)

class TransformMatrix:
    rows = None
    def __init__(self, *rows):
        self.rows = rows
    def toOpenSCAD(self):
        return "[" + ",".join(
            ("[" + ",".join(repr(f) for f in row) + "]") for row in self.rows
        ) + "]"
    def __repr__(self):
        return "TransformMatrix(" + ",".join(repr(r) for r in self.rows) + ")"
    def __mul__(self, other):
        a = self
        b = other
        m = len(a.rows)
        n = len(b.rows)
        assert len(a.rows[0]) == n
        p = len(b.rows[0])
        c = [[0 for col in xrange(p)] for row in xrange(m)]
        for i in xrange(m):
            for j in xrange(p):
                for k in xrange(n):
                    c[i][j] += a.rows[i][k] * b.rows[k][j]
        return TransformMatrix(*c)
    def transform(self, point):
        m = self * TransformMatrix([point.x],[point.y],[point.z],[1])
        return Point(m.rows[0][0], m.rows[1][0], m.rows[2][0])
    def toSVG(self):
        # The simpletransform.py code represents 2d affine matrices by
        # omitting the final [0,0,1] row
        return [
            [self.rows[0][0], self.rows[0][1], self.rows[0][3]],
            [self.rows[1][0], self.rows[1][1], self.rows[1][3]],
        ]
    @staticmethod
    def fromSVG(mat):
        return TransformMatrix(
            [mat[0][0], mat[0][1], 0, mat[0][2]],
            [mat[1][0], mat[1][1], 0, mat[1][2]],
            [0, 0, 1, 0],
            [0, 0, 0, 1]
        )
    @staticmethod
    def identity():
        return TransformMatrix.translate(Point(0,0,0))

    @staticmethod
    def translate(vector):
        return TransformMatrix(
            [1, 0, 0, vector.x],
            [0, 1, 0, vector.y],
            [0, 0, 1, vector.z],
            [0, 0, 0, 1]
        )
    @staticmethod
    def scale(amt):
        if not isinstance(amt, Point):
            amt = Point(amt, amt, amt)
        return TransformMatrix(
            [amt.x, 0, 0, 0],
            [0, amt.y, 0, 0],
            [0, 0, amt.z, 0],
            [0, 0, 0, 1]
        )
    @staticmethod
    def rotateAxis(axis, angle=None, sin=None, cos=None):
        c = math.cos(angle) if cos is None else cos
        s = (safe_sqrt(1-c*c) if angle is None else math.sin(angle)) \
            if sin is None else sin
        # we don't need information about the sign of s if it is
        # implicit in the direction of 'axis'
        x, y, z = axis.x, axis.y, axis.z
        C = 1 - c
        return TransformMatrix(
            [c + x*x*C, x*y*C - z*s, x*z*C + y*s, 0],
            [y*x*C + z*s, c + y*y*C, y*z*C - x*s, 0],
            [z*x*C - y*s, z*y*C + x*s, c + z*z*C, 0],
            [0, 0, 0, 1]
        )

class Shape:
    faces = None
    planes = None
    def __init__(self, name, faces):
        self.name = name
        self.faces = faces
        self.planes = [f.plane() for f in self.faces]
    def representativeFace(self):
        """Return one face which will represent this particular symmetry
        group.  Polyhedra with multiple symmetry groups will override
        this method to define one Shape object for each symmetry group."""
        return self.faces[0]
    @staticmethod
    def faceTransform(face1, face2):
        """Transform from face1 to face2"""
        # First transform to the right plane
        xform = Shape.planeTransform(face1.plane(), face2.plane())
        # Now rotate so the first points in the face correspond
        p1, p2 = face1.points[0].transform(xform), face2.points[0]
        center = face2.centroid()
        p1v, p2v = (p1-center).normalized(), (p2-center).normalized()
        axis = p1v.cross(p2v) # this is `center`, but maybe w/ opposite sign
        if near_zero(axis.dist()): axis = center
        axis = axis.normalized()
        rotCos = p1v.dot(p2v)
        return TransformMatrix.rotateAxis(axis, cos=rotCos) \
            * xform
    @staticmethod
    def planeTransform(plane1, plane2):
        # if the planes are different distances from the origin we may need
        # to scale or translate at the end
        assert near_zero(plane1.d() - plane2.d())
        axis = plane1.normal.cross(plane2.normal)
        if near_zero(axis.dist()): # planes are parallel
            axis = (plane1.arbitrary_point() - plane1.point)
        axis = axis.normalized()
        rotCos = plane1.normal.dot(plane2.normal)
        return TransformMatrix.rotateAxis(axis, cos=rotCos)

class Dodecahedron(Shape):
    """A dodecahedron."""
    def __init__(self, diameter=1):
        def mkTop(i):
            i = i % 5
            return Point(2*cos((2/5)*pi*i), 2*sin((2/5)*pi*i), phi+1)
        def mkBot(i):
            i = i % 5
            return Point(2*phi*cos((2/5)*pi*i), 2*phi*sin((2/5)*pi*i), phi-1)
        faces = [
            Face(*[mkTop(i) for i in xrange(0, 5)])
        ] + [
            Face(mkBot(i+1), mkTop(i+1), mkTop(i), mkBot(i), -mkBot(i+3)) for i in xrange(0, 5)
        ] + [
            Face(-mkBot(i), -mkTop(i), -mkTop(i+1), -mkBot(i+1), mkBot(i+3)) for i in xrange(0, 5)
        ] + [
            Face(*[-mkTop(4 - i) for i in xrange(0,5)])
        ]
        # Rotate faces for tetrahedral symmetry
        for i,j in [(0,0),(1,1),(2,4),(3,0),(4,2),(5,3),
                    (6,1),(7,3),(8,2),(9,0),(10,4),(11,3)]:
            faces[i] = faces[i].shuffle(j)
        Shape.__init__(self, "Dodecahedron", [f*(diameter/4) for f in faces])

class Icosahedron(Shape):
    """An icosahedron."""
    def __init__(self, diameter=1):
        peaks = [Point(0,0,1), Point(0,0,-1)]
        r = (2/5)*sqrt(5)
        h = (1/5)*sqrt(5)
        top = [Point(r*cos(2*pi*i/5), r*sin(2*pi*i/5), h) for i in xrange(6)]
        bot = [Point(r*cos(2*pi*(i+.5)/5), r*sin(2*pi*(i+.5)/5), -h) for i in xrange(6)]
        faces = [
            Face(peaks[0], top[i], top[i+1]) for i in xrange(0,5)
        ] + [
            Face(top[i], bot[i], top[i+1]) for i in xrange(0,5)
        ] + [
            Face(bot[i+1], top[i+1], bot[i]) for i in xrange(0,5)
        ] + [
            Face(peaks[1], bot[i+1], bot[i]) for i in xrange(0,5)
        ]
        Shape.__init__(self, "Icosahedron", [f*(diameter/2) for f in faces])

class RhombicDodecahedron(Shape):
    """A rhombic dodecahedron."""
    def __init__(self, diameter=1):
        def mkFace(*args): return Face(*[Point(*a) for a in args])
        # top four faces
        faces = [
            mkFace([-1,-1,1],[0,-2,0],[1,-1,1],[0,0,2]).transform(
                TransformMatrix.rotateAxis(Point(0,0,1), angle=i*pi/2)
            ) for i in xrange(0,4)
        ]
        # two middle faces
        faces += [
            mkFace([-1,-1,-1],[0,-2,0],[-1,-1,1],[-2,0,0]).transform(
                TransformMatrix.rotateAxis(Point(0,0,1), angle=i*pi/2)
            ) for i in xrange(0,4,2)
        ]
        # bottom four+two faces
        faces += [f.transform(
            TransformMatrix.rotateAxis(Point(1,0,0), angle=pi)
        ) for f in faces]
        Shape.__init__(self, "Rhombic Dodecahedron", [f*(diameter/4) for f in faces])

def name_to_shape(str, diameter, default="dodecahedron"):
    if str.lower() == 'dodecahedron':
        return Dodecahedron(diameter)
    if str.lower() == 'icosahedron':
        return Icosahedron(diameter)
    if str.lower() == 'rd' or str.lower() == 'rhombic dodecahedron':
        return RhombicDodecahedron(diameter)
    # Default
    return name_to_shape(default, diameter)

### Your main function subclasses the inkex.Effect class

class LayerSettings:
    effect = None
    layer = None
    metaLayer = None
    markingsLayer = None

    shape = None
    symmetry = 1
    frontThick = None
    backThick = None

    origin = None
    translation = Point(0, 0, 0)
    scaleFactor = 1

    pageFace = None # used to establish rotation to page
    pageToShapeXform = None
    shapeToPageXform = None

    def __init__(self, effect, layer):
        self.effect = effect
        self.layer = layer
        self.metaLayer = effect.ensure_layer(layer, 'Meta')
        self.markingsLayer = effect.ensure_layer(layer, 'Markings', locked=False)
        self.parse_meta()
        self.parse_origin()
        face = self.shape.representativeFace()
        zheight = face.centroid().dist()
        self.pageFace = Face(
            Point(0, 1,zheight),
            Point(-1,0,zheight),
            Point(0,-1,zheight),
            Point( 1,0,zheight)
        )
        self.pageToShapeXform = (
            Shape.faceTransform(self.pageFace, face) *
            TransformMatrix.scale(Point(1,-1,1)) *
            TransformMatrix.translate(-self.origin + self.pageFace.centroid()))
        # XXX in theory this should just be a matrix inverse
        self.shapeToPageXform = (
            TransformMatrix.translate(self.origin - self.pageFace.centroid()) *
            TransformMatrix.scale(Point(1,-1,1)) *
            Shape.faceTransform(face, self.pageFace)
            )

    def toString(self, obj):
        return json.dumps(obj, indent=2)

    def fromString(self, str, default=None):
        if str is None:
            return default
        try:
            return json.loads(str)
        except ValueError:
            return default

    def parse_meta(self):
        DEFAULT_PLANE = {
            'shape': 'rhombic dodecahedron',
            'size': '3in',
            'symmetry': '1',
            'frontThick': '.125in',
            'backThick': '0',
        }
        # Look for the text element
        els = self.metaLayer.xpath(
            "./svg:text[@data-stellation]", namespaces=inkex.NSS
        )
        if len(els) > 0:
            textEl = els[0]
        else:
            textEl = inkex.etree.SubElement(
                self.metaLayer, inkex.addNS('text', 'svg'), {
                inkex.addNS('label', 'inkscape'): 'Annotation',
                'style': simplestyle.formatStyle({
                    'font-size': '20px',
                    'font-style': 'normal',
                    'font-weight': 'normal',
                    'fill': '#000',
                    'font-family': 'sans-serif',
                    'text-anchor': 'left',
                    'text-align': 'left',
                }),
                'x': '0',
                'y': '0',
            })
            textEl.text = self.toString(DEFAULT_PLANE)
        newData = self.fromString(textEl.text, DEFAULT_PLANE)
        oldData = self.fromString(textEl.get('data-stellation'), None)
        newDiameter = self.effect.unittouu(
            newData.get('size', DEFAULT_PLANE['size'])
        )
        self.shape = name_to_shape(
            newData.get('shape', DEFAULT_PLANE['shape']), newDiameter
        )
        if oldData is not None and oldData.has_key('size'):
            oldDiameter = self.effect.unittouu(oldData['size'])
            # Rescale objects if size has changed
            self.scaleFactor = newDiameter / oldDiameter
        if newData.get('symmetry') is not None:
            self.symmetry = int(newData['symmetry'])
        self.frontThick = self.effect.unittouu(newData.get('frontThick', '3mm'))
        self.backThick = self.effect.unittouu(newData.get('backThick', '0'))
        textEl.set('data-stellation', self.toString(newData))

    def parse_origin(self):
        # Look for the origin element
        els = self.metaLayer.xpath(
            "./svg:circle[@data-stellation]", namespaces=inkex.NSS
        )
        if len(els) > 0:
            circle = els[0]
        else:
            docHeight = self.effect.unittouu(self.effect.getDocumentHeight())
            docWidth = self.effect.unittouu(self.effect.getDocumentWidth())
            circle = inkex.etree.SubElement(
                self.metaLayer, inkex.addNS('circle','svg'), {
                'style': simplestyle.formatStyle({
                    'stroke': 'none',
                    'fill': 'black',
                }),
                'cx': str(docWidth/2),
                'cy': str(docHeight/2),
                'r': str(self.effect.unittouu("3mm")),
                inkex.addNS('label','inkscape'): 'Origin',
            });
        self.origin = Point(
            float(circle.get('cx')),
            float(circle.get('cy')),
            0
        )
        prev = self.fromString(circle.get('data-stellation'))
        if prev is not None:
            oldOrigin = Point(float(prev.get('x', self.origin.x)),
                              float(prev.get('y', self.origin.y)),
                              0)
            self.translation = self.origin - oldOrigin

        circle.set('data-stellation', self.toString({
            'x': self.origin.x,
            'y': self.origin.y,
        }))

class StellationEffect(inkex.Effect):

    def __init__(self):
        " define how the options are mapped from the inx file "
        inkex.Effect.__init__(self) # initialize the super class
        self.OptionParser.add_option(
            '--add', action="store", type="inkbool", dest="add", default=False,
            help="Add a new plane"
        )
        self.OptionParser.add_option(
            '--output', action="store", type="string", dest="output", default=None,
            help="Export OpenSCAD file"
        )

### -------------------------------------------------------------------
### This is your main function and is called when the extension is run.

    def effect(self):
        output = open(
            os.path.expanduser( self.options.output ).replace('/', os.sep),
            'w'
        ) if self.options.output else None

        if self.options.add:
            self.add_new_plane()
        for layer in self.stellation_layers():
            self.update_layer(layer, output=output)

    def update_layer(self, layer, output=None):
        settings = LayerSettings(self, layer)
        self.update_layer_xform(settings)
        self.update_layer_guidelines(settings)
        self.update_layer_symmetry(settings)
        self.update_layer_intersections(settings)
        if output is not None:
            self.openscadLayer(settings, output)

    def update_layer_xform(self, settings):
        scale = (
            TransformMatrix.translate(settings.origin) *
            TransformMatrix.scale(settings.scaleFactor) *
            TransformMatrix.translate(-settings.origin)
        )
        mat = (scale * TransformMatrix.translate(settings.translation)).toSVG()
        for layer in [settings.markingsLayer, settings.layer]:
            for node in self.layer_contents(layer):
                simpletransform.applyTransformToNode(mat, node)

    def update_layer_guidelines(self, settings):
        guidelines = self.ensure_layer(settings.layer, 'Guidelines')
        # save style from existing guidelines (if any)
        els = guidelines.xpath("./svg:path", namespaces=inkex.NSS)
        oldStyle = None if len(els) < 1 else els[0].get('style')
        # delete all existing guidelines
        self.delete_layer_contents(guidelines)
        # compute new guidelines
        face = settings.shape.representativeFace()
        path = ""
        for plane in settings.shape.planes:
            line = face.plane().intersect(plane)
            if line is None: continue # parallel plane
            # Compute points where this line intersects the page bounding planes
            line = line.transform(settings.shapeToPageXform)
            pts = [line.intersect(p) for p in self.pagePlanes()]
            # filter out points outside page face
            pageFace = self.pageFace()
            pts = [p for p in pts if
                   p is not None and
                   p.x + EPSILON >= pageFace.points[0].x and
                   p.y + EPSILON >= pageFace.points[0].y and
                   p.x - EPSILON <= pageFace.points[2].x and
                   p.y - EPSILON <= pageFace.points[2].y]
            if len(pts) == 0: continue # line outside of page bounds
            assert len(pts) == 2
            path += "M %f,%f L %f,%f " % (pts[0].x, pts[0].y, pts[1].x, pts[1].y)
        inkex.etree.SubElement(guidelines, inkex.addNS('path', 'svg'),{
            'd': path,
            'style': simplestyle.formatStyle({
                'opacity': 1,
                'fill': 'none',
                'stroke': 'red',
                'stroke-width': 1,
                'stroke-linecap': 'butt',
            }) if oldStyle is None else oldStyle,
        })

    def update_layer_symmetry(self, settings):
        symmetry = self.ensure_layer(settings.layer, 'Symmetry')
        # delete all existing stuff
        self.delete_layer_contents(symmetry)
        for i in xrange(settings.symmetry - 1):
            g = inkex.etree.SubElement(symmetry, inkex.addNS('g', 'svg'), {
                'transform':'rotate(%f %f %f)' % (
                    360*(i+1)/settings.symmetry,
                    settings.origin.x,
                    settings.origin.y,
                )
            })
            for layer in [settings.markingsLayer, settings.layer]:
                for node in self.layer_contents(layer):
                    id = node.get('id')
                    if id is None:
                        # ensure there's an id
                        id = self.uniqueId('stella');
                        node.set('id', id)
                    inkex.etree.SubElement(g, inkex.addNS('use', 'svg'), {
                        inkex.addNS('href','xlink'): '#' + id,
                    })

    def update_layer_intersections(self, settings):
        intersectionLayer = self.ensure_layer(settings.layer, 'Intersections')
        # save style from existing intersections (if any)
        els = intersectionLayer.xpath("./svg:path", namespaces=inkex.NSS)
        oldStyle = None if len(els) < 1 else els[0].get('style')
        # delete all existing intersections
        self.delete_layer_contents(intersectionLayer)
        # simplify / rotate shape
        # XXX should probably do the shape parsing *once*, and then
        # rotate / reuse for openSCAD export / etc (store in settings?)
        paths = []
        for i in xrange(settings.symmetry):
            xform = (
                TransformMatrix.translate(settings.origin) *
                TransformMatrix.rotateAxis(
                    Point(0,0,1), angle=2*pi*i/settings.symmetry
                ) *
                TransformMatrix.translate(-settings.origin)
            )
            for node in self.layer_contents(settings.layer):
                if node.tag == inkex.addNS('path', 'svg'):
                    xform2 = xform * TransformMatrix.fromSVG(
                        simpletransform.parseTransform(node.get('transform'))
                    )
                    d = node.get('d')
                    p = cubicsuperpath.parsePath(d)
                    cspsubdiv.cspsubdiv(p, FLATNESS)
                    for sp in p:
                        thisPath = []
                        for csp in sp:
                            thisPath.append(
                                Point(csp[1][0],csp[1][1],0).transform(xform2)
                            )
                        paths.append(thisPath)
        # compute new intersections
        face = settings.shape.representativeFace()
        d1,d2 = "",""
        for other_face,scootch in \
            [(f,s) for f in settings.shape.faces for s in [1,-1]]:
            line = face.plane().intersect(other_face.plane())
            if line is None: continue # parallel plane
            # Compute points where this line intersects the figure
            other_line = line.transform(
                settings.shapeToPageXform *
                Shape.faceTransform(other_face, face)
            )
            # tweak this line just a scootch toward/away from the origin
            # we'll use opacity so these overlapped regions show up nice
            other_line = Line(other_line.point +
                              (other_line.point - settings.origin) *
                              self.unittouu(SCOOTCH) * scootch,
                              other_line.direction)
            intersections = []
            for path in paths:
                for i in xrange(len(path)-1):
                    p0,p1 = path[i],path[i+1]
                    # compute 2d intersection of l with segment(p1->p2)
                    for s in other_line.intersect2dSegment(p0, p1):
                        intersections.append(s)
            # now transform these intersections into points on the original line
            intersections = [(line.point + line.direction * s)
                             for s in sorted(intersections)]
            # XXX should make the thicknesses match
            intersections = [pt.transform(settings.shapeToPageXform)
                             for pt in intersections]
            # XXX this is projection, but should really widen to account for
            # angle
            front = (line.point + other_face.plane().normal).transform(
                settings.shapeToPageXform
            ) - line.point.transform(settings.shapeToPageXform)
            front = Point(front.x, front.y, 0).normalized()

            for i in xrange(0, len(intersections), 2):
                s,e = intersections[i], intersections[i+1]
                p0 = s + front*settings.frontThick
                p1 = e + front*settings.frontThick
                p2 = e - front*settings.backThick
                p3 = s - front*settings.backThick
                dd = "M %f,%f L %f,%f L %f,%f L %f,%f Z " % (
                    p0.x, p0.y,
                    p1.x, p1.y,
                    p2.x, p2.y,
                    p3.x, p3.y,
                )
                if scootch > 0:
                    d1 += dd
                else:
                    d2 += dd
        oldStyle = None
        inkex.etree.SubElement(intersectionLayer, inkex.addNS('path', 'svg'), {
            'd': d1,
            'style': simplestyle.formatStyle({
                'opacity': 0.5,
                'fill': 'blue',
                'stroke': 'none',
                'stroke-width': 2,
            }) if oldStyle is None else oldStyle,
        })
        inkex.etree.SubElement(intersectionLayer, inkex.addNS('path', 'svg'), {
            'd': d2,
            'style': simplestyle.formatStyle({
                'opacity': 0.5,
                'fill': 'blue',
                'stroke': 'none',
                'stroke-width': 2,
            }) if oldStyle is None else oldStyle,
        })

    def delete_layer_contents(self, layer):
        attribs = layer.items()
        layer.clear()
        for key, value in attribs:
            layer.set(key, value)

    def layer_contents(self, layer):
        for child in layer:
            if child.get(inkex.addNS('groupmode', 'inkscape')) == 'layer':
                continue
            yield child

    def add_new_plane(self):
        svg = self.document.getroot()
        layer = inkex.etree.SubElement(svg, inkex.addNS('g', 'svg'), {
            inkex.addNS('label', 'inkscape'): 'Plane',
            inkex.addNS('groupmode', 'inkscape'): 'layer',
            'data-stellation': 'plane',
        })

    def stellation_layers(self):
        return self.document.xpath("//svg:svg/svg:g[@data-stellation='plane']", namespaces=inkex.NSS)

    def ensure_layer(self, parent, name, locked=True):
        for g in parent.xpath('./svg:g', namespaces=inkex.NSS):
            if g.get(inkex.addNS('label', 'inkscape')) == name:
                return g
        layer = inkex.etree.SubElement(parent, inkex.addNS('g', 'svg'), {
            inkex.addNS('label', 'inkscape'): name,
            inkex.addNS('groupmode', 'inkscape'): 'layer',
        })
        if locked:
            layer.set(inkex.addNS('insensitive', 'sodipodi'), 'true')
        return layer

    def pageFace(self):
        """Return a face representing the document."""
        viewbox = [
            float(s) for s in self.document.getroot().get('viewBox').split()
        ]
        return Face(
            Point(viewbox[0],viewbox[1],0),
            Point(viewbox[2],viewbox[1],0),
            Point(viewbox[2],viewbox[3],0),
            Point(viewbox[0],viewbox[3],0),
            inside=Point(0,0,-1)
        )

    def pagePlanes(self):
        """Return four planes representing the edges of the document."""
        pageFace = self.pageFace()
        return [
            Plane(pageFace.points[0], Vector( 0,-1, 0)),
            Plane(pageFace.points[0], Vector(-1, 0, 0)),
            Plane(pageFace.points[2], Vector( 0, 1, 0)),
            Plane(pageFace.points[2], Vector( 1, 0, 0)),
        ]

    def openscadLayer(self, settings, f):
        def mm(val):
            if isinstance(val, Point):
                return Point(mm(val.x), mm(val.y), mm(val.z))
            return self.uutounit(val, "mm")
        face = settings.shape.representativeFace()
        f.write( 'module layer_%s() {\n' % settings.layer.get('id') )
        f.write( '  scale(1/%f)\n' % mm(1))
        f.write( '  multmatrix(m=%s)\n' % (
            settings.pageToShapeXform *
            TransformMatrix.translate(settings.origin)).toOpenSCAD()
        )
        f.write( '  for (i=[0:%d]) rotate(i*360/%d)\n' % (
            settings.symmetry-1, settings.symmetry
        ))
        f.write( '  translate([0,0,%f]) linear_extrude(height=%f)\n' %
                 ( -settings.backThick,
                   settings.frontThick+settings.backThick ) )
        points = []
        pointMap = {}
        def pointToIdx(x, y, xform):
            key = repr((x,y))
            if not pointMap.has_key(key):
                pointMap[key] = len(points)
                points.append(Point(x,y,0).transform(xform)-settings.origin)
            return pointMap[key]
        paths = []
        for node in self.layer_contents(settings.layer):
            # emit openscad polygon
            if node.tag == inkex.addNS('path','svg'):
                xform = TransformMatrix.fromSVG(
                    simpletransform.parseTransform(node.get('transform'))
                )
                pointMap.clear() # new xform, clear pointMap
                d = node.get('d')
                p = cubicsuperpath.parsePath(d)
                cspsubdiv.cspsubdiv(p, FLATNESS)
                for sp in p:
                    thisPath = []
                    for csp in sp:
                        idx = pointToIdx(csp[1][0],csp[1][1],xform)
                        thisPath.append(idx)
                    paths.append(thisPath)
            # XXX we should probably parse g, circle, rect, etc
        f.write('    polygon(points=[%s], paths=[%s]);\n' %
                (",".join(("[%f,%f]" % (p.x, p.y)) for p in points),
                 ",".join(
                     ("[" + ",".join(("%d" % i) for i in subpath) + "]") for
                     subpath in paths
                     )))
        f.write( '}\n' )
        f.write( 'faceMatrix = [\n' )
        f.write( '  ' + ',\n  '.join(
            Shape.faceTransform(settings.shape.representativeFace(), face)
            .toOpenSCAD()
            for face in settings.shape.faces
        ) + '];\n')
        f.write( 'faceColors = [\n' )
        f.write( ' "black", "brown", "red", "orange", "yellow",\n' );
        f.write( ' "green", "blue", "violet", "grey", "white",\n' );
        f.write( ' "white", "white", "white", "white"\n' );
        f.write( '];\n');
        f.write( 'for (f=[0:%d]) {\n' % (len(settings.shape.faces)-1) )
        #f.write( '  echo(f=f);\n')
        f.write( '  color(faceColors[f%len(faceColors)])\n')
        f.write( '  multmatrix(m=faceMatrix[f])\n')
        f.write( '  layer_%s();\n' % settings.layer.get('id') )
        f.write( '}\n')

if __name__ == '__main__':
    e = StellationEffect()
    e.affect()
