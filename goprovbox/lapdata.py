"""Ground-distance geometry and reconstructed session lap timing.

VBO Start stores two successive points describing the direction through a
gate, not the two ends of a line across the circuit. The gate is perpendicular.
"""
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
import math
import re


class Projection:
    def __init__(self, lat, lon):
        self.lat, self.lon = lat, lon
        self.xscale = 111_195.0802 * math.cos(math.radians(lat))
    def point(self, lat, lon):
        return ((lon-self.lon)*self.xscale, (lat-self.lat)*111_195.0802)


@dataclass
class Lap:
    start: float
    end: float
    number: int
    times: list
    distances: list
    points: list
    @property
    def duration(self): return self.end-self.start


class Session:
    def __init__(self, vbo):
        self.vbo = vbo
        self.times = [r.utc for r in vbo.rows]
        self.indices = {c:i for i,c in enumerate(vbo.columns)}
        self.lat = [float(r.values[vbo.index("lat")])/60 for r in vbo.rows]
        self.lon = [-float(r.values[vbo.index("long")])/60 for r in vbo.rows]
        self.projection = Projection(sum(self.lat)/len(self.lat), sum(self.lon)/len(self.lon))
        self.points = [self.projection.point(a,b) for a,b in zip(self.lat,self.lon)]
        self.xs, self.ys = map(list,zip(*self.points))
        self.gaps = [b for a,b in zip(self.times,self.times[1:]) if b-a>.5]
        self.speed = [float(r.values[vbo.index("velocity")])/3.6 for r in vbo.rows]
        self.heading = [float(r.values[vbo.index("heading")]) for r in vbo.rows]
        self.crossings = self.find_crossings()
        self.laps = []
        for number, (start,end) in enumerate(zip(self.crossings,self.crossings[1:]),1):
            if end-start < 10 or any(start < gap <= end for gap in self.gaps):
                continue
            times=[start]+[t for t in self.times if start<t<end]+[end]
            pts=[self.position(t) for t in times]
            distance=[0.0]
            for p,q in zip(pts,pts[1:]):distance.append(distance[-1]+math.dist(p,q))
            self.laps.append(Lap(start,end,number,times,distance,pts))
        self.map_points = min(self.laps,key=lambda l:l.duration).points if self.laps else self.points

    def interpolate(self, values, utc):
        i=bisect_left(self.times,utc)
        if i<len(self.times) and abs(self.times[i]-utc)<1e-6:return values[i]
        if not 0<i<len(self.times) or self.times[i]-self.times[i-1]>.5:return None
        f=(utc-self.times[i-1])/(self.times[i]-self.times[i-1])
        return values[i-1]*(1-f)+values[i]*f

    def position(self, utc):
        return (self.interpolate(self.xs,utc),self.interpolate(self.ys,utc))

    def find_crossings(self):
        line=next((p for p in self.vbo.preamble if re.match(r"^Start\s",p,re.I)),None)
        if line is None:return []
        try:
            lon1,lat1,lon2,lat2=map(float,line.split()[1:5])
            a=self.projection.point(lat1/60,-lon1/60);b=self.projection.point(lat2/60,-lon2/60)
        except (ValueError,IndexError):return []
        dx,dy=b[0]-a[0],b[1]-a[1];length=math.hypot(dx,dy)
        if length<.1:return []
        direction=(dx/length,dy/length);centre=((a[0]+b[0])/2,(a[1]+b[1])/2)
        candidates=[]
        for i,(p,q) in enumerate(zip(self.points,self.points[1:])):
            if self.times[i+1]-self.times[i]>.5 or self.speed[i]<5/3.6:continue
            before=(p[0]-centre[0])*direction[0]+(p[1]-centre[1])*direction[1]
            after=(q[0]-centre[0])*direction[0]+(q[1]-centre[1])*direction[1]
            if before*after>0 or before==after:continue
            f=before/(before-after)
            # Include both endpoints: floating-point cancellation can round a
            # crossing to exactly 1 while the next segment starts just above 0.
            # The debounce below removes a crossing seen by both segments.
            if not 0<=f<=1:continue
            x=p[0]+f*(q[0]-p[0])-centre[0];y=p[1]+f*(q[1]-p[1])-centre[1]
            if abs(x*direction[1]-y*direction[0])>12.5:continue
            candidates.append((self.times[i]+f*(self.times[i+1]-self.times[i]),1 if after>before else -1))
        if not candidates:return []
        # Reject reverse passes; the predominant driving direction wins.
        direction=max((-1,1),key=lambda s:sum(sign==s for _,sign in candidates))
        result=[]
        for utc,sign in candidates:
            if sign==direction and (not result or utc-result[-1]>=10):result.append(utc)
        return result

    def acceleration(self, utc, lateral=False):
        # A centred 0.4 s GPS window avoids amplifying individual sample noise.
        a=max(self.times[0],utc-.2);b=min(self.times[-1],utc+.2)
        if b-a<.05 or any(a<gap<=b for gap in self.gaps):return None
        va=self.interpolate(self.speed,a);vb=self.interpolate(self.speed,b)
        if va is None or vb is None:return None
        if not lateral:return (vb-va)/(b-a)
        ia=max(0,bisect_left(self.times,a));ib=min(len(self.times)-1,bisect_left(self.times,b))
        turn=(self.heading[ib]-self.heading[ia]+180)%360-180
        return -(va+vb)/2*math.radians(turn)/(self.times[ib]-self.times[ia]) if ib>ia else None

    def lap_values(self, utc):
        completed=[lap for lap in self.laps if lap.end<=utc]
        previous=completed[-1] if completed else None
        best=min(completed,key=lambda l:l.duration) if completed else None
        idx=bisect_right(self.crossings,utc)-1
        start=self.crossings[idx] if idx>=0 else None
        valid=start is not None and not any(start<gap<=utc for gap in self.gaps)
        elapsed=utc-start if valid else None
        delta=None
        if best and valid:
            # Compare location on the reference lap, not percentage of a lap
            # learned from the future. Restrict candidates by vehicle heading
            # and proximity so a nearby opposing section cannot be selected.
            point=self.position(utc)
            heading=self.heading[min(len(self.heading)-1,bisect_left(self.times,utc))]
            direction=(math.sin(math.radians(heading)),math.cos(math.radians(heading)))
            nearest=None
            for j,(p,q) in enumerate(zip(best.points,best.points[1:])):
                dx,dy=q[0]-p[0],q[1]-p[1];length2=dx*dx+dy*dy
                if length2<.01 or dx*direction[0]+dy*direction[1]<0:continue
                f=max(0,min(1,((point[0]-p[0])*dx+(point[1]-p[1])*dy)/length2))
                distance=(point[0]-p[0]-f*dx)**2+(point[1]-p[1]-f*dy)**2
                if nearest is None or distance<nearest[0]:nearest=(distance,best.times[j]+f*(best.times[j+1]-best.times[j])-best.start)
            if nearest and nearest[0]<=30**2:delta=elapsed-nearest[1]
        return {"lap_number": previous.number if previous else None, "lap_time": elapsed,
                "previous_lap": previous.duration if previous else None, "best_lap": best.duration if best else None, "delta":delta}
