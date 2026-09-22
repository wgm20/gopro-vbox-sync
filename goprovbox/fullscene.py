"""Full data scene: artwork, logged channels, lap timing, GPS map and G-ball."""
from bisect import bisect_left
from datetime import datetime, timezone, timedelta
import io
import math
import re

from PIL import Image, ImageDraw, ImageFilter

from .gpmf import TelemetryError
from .overlay import parse_scene, scene_xml, numbers, datasource, scene_number, FourChannelRenderer, Timeline
from .lapdata import Session


def parse_full_scene(members, name="Scene", digest=""):
    result=parse_scene(members,name,digest)
    root=scene_xml(members["SCENE.XML"]).find(".//scene")
    config=scene_xml(members["VBOXHD.XML"])
    images={e.get("name"):e.get("path") for e in root.findall("images/image")}
    def image(name):
        path=images[name]
        if path not in result.assets:
            im=Image.open(io.BytesIO(members[path]))
            if im.format!="PNG" or im.width*im.height>16_000_000:
                raise TelemetryError("Unsupported scene artwork")
            result.assets[path]=im.convert("RGBA")
        return result.assets[path]
    graphics={e.get("name"):e for e in root.find("graphics")}
    four={e.get("name"):e for e in result.elements if e.get("name")}
    elements=[];omitted=[]
    try:
        for item in sorted(root.findall("layouts/layout/graphic"),key=lambda e:int(e.get("zorder",0))):
            name_=item.get("name");e=graphics[name_]
            pos=tuple(int(v) for v in numbers(item.get("screen_pos")))
            if len(pos)!=2 or any(abs(v)>16384 for v in pos):raise TelemetryError("Invalid scene position")
            if "pip" in name_.lower():omitted.append(name_);continue
            if e.tag=="static_image":
                im=image(e.get("background"));element={"kind":"image","image":im,"size":im.size}
            elif e.tag=="text":
                box=numbers(e.get("offset"));font=result.fonts[e.get("font")]
                fmt=e.get("fmtstr","%d");fmtfn=e.get("fmtfn","")
                if len(box)!=4 or min(box[2:])<=0 or max(box[2:])>8192:raise TelemetryError("Invalid scene text box")
                if fmtfn not in ("valid_time_ms","time_hm") and not re.fullmatch(r"%[+ 0]?\d{0,2}(?:\.\d{1,2})?(?:l|ll|h)?[diuf]",fmt):
                    raise TelemetryError(f"Unsupported number format in {name_}")
                element={"kind":"text","font":font,"size":(int(box[2]),int(box[3])),"box":box,
                         "fmt":re.sub(r"[lh]","",fmt).replace("u","d"),"fmtfn":fmtfn,
                         "source":datasource(e.get("datasource","")),"justification":e.get("justification","middle_right")}
            elif e.tag=="multibar" and name_ in four:
                element=dict(four[name_]);element["source"]=datasource(e.get("datasource",""))
            elif e.tag=="xyplot":
                im=image(e.get("background"));marker=image(e.get("foreground"))
                xr=numbers(e.get("x_range"));yr=numbers(e.get("y_range"))
                if len(xr)!=2 or len(yr)!=2 or xr[1]<=xr[0] or yr[1]<=yr[0]:raise TelemetryError("Invalid G-ball range")
                element={"kind":"xyplot","image":im,"marker":marker,"size":im.size,"xr":xr,"yr":yr,
                         "xsource":datasource(e.get("x_datasource","")),"ysource":datasource(e.get("y_datasource",""))}
            elif e.tag=="map":
                size=tuple(int(v) for v in numbers(e.get("reserved_size","(480,480)")))
                if len(size)!=2 or min(size)<32 or max(size)>2048:raise TelemetryError("Invalid track map size")
                point=e.find("dynamic_point")
                element={"kind":"map","size":size,"marker":image(point.get("image")) if point is not None else None}
            else:
                raise TelemetryError(f"Full scene does not yet support the {e.tag} widget '{name_}'")
            element.update(name=name_,pos=pos,z=int(item.get("zorder",0)));elements.append(element)
        result.elements=elements;result.omitted=omitted+[e.get("name","camera") for e in root.findall("video_mappings/video_mapping")]
        result.mode="full"
        time=config.find(".//times")
        offset=int(time.get("utc_local_offset_s",0)) if time is not None else 0
        if abs(offset)>=86400:raise TelemetryError("Invalid scene timezone offset")
        result.options={"utc_offset":offset,"notes":[
            "Lap timing is reconstructed from the full source VBO, using its Start direction and a 25 m perpendicular gate; missing or incomplete laps show dashes.",
            "Delta is reconstructed against the fastest previously completed valid lap by GPS position; it is not the recorder's stored OLED prediction.",
            "G-ball GPS acceleration is calculated over a 0.4 s window; recorder smoothing may differ.",
            "The track outline comes from a complete recorded lap with equal east/north metre scales, rather than the vendor's track database.",
            f"Date/time follows the scene's fixed UTC offset ({offset:+d} seconds)."]}
        return result
    except (KeyError,AttributeError,TypeError,ValueError,IndexError,OSError) as exc:
        raise TelemetryError(f"Cannot read the full scene: {exc}") from exc


def fit_layout(elements, canvas, width, height):
    """Scale all widgets uniformly; keep connected panels together at their edge.

    Surplus room from square/portrait footage moves groups, never stretches them.
    Bounding boxes include all label/text extents and an inset prevents clipping.
    """
    left=min(0,min(e["pos"][0] for e in elements));top=min(0,min(e["pos"][1] for e in elements))
    cw=max(canvas[0],max(e["pos"][0]+e["size"][0] for e in elements))-left
    ch=max(canvas[1],max(e["pos"][1]+e["size"][1] for e in elements))-top
    margin=max(2,round(min(width,height)*.015))
    scale=min((width-2*margin)/cw,(height-2*margin)/ch)
    if scale<=0:raise TelemetryError("Video is too small for the scene")
    groups=[]
    for i,e in enumerate(elements):
        x,y=e["pos"];w,h=e["size"];groups.append(({i},[x,y,x+w,y+h]))
    changed=True
    while changed:
        changed=False
        for i in range(len(groups)):
            for j in range(i+1,len(groups)):
                a,b=groups[i][1],groups[j][1]
                if a[0]<=b[2]+12 and a[2]+12>=b[0] and a[1]<=b[3]+12 and a[3]+12>=b[1]:
                    groups[i]=(groups[i][0]|groups[j][0],[min(a[0],b[0]),min(a[1],b[1]),max(a[2],b[2]),max(a[3],b[3])])
                    groups.pop(j);changed=True;break
            if changed:break
    extra_x=width-2*margin-cw*scale;extra_y=height-2*margin-ch*scale
    fitted=[dict(e) for e in elements]
    for indices,bounds in groups:
        def anchor(mid,total):return 0 if mid<total/3 else 1 if mid>2*total/3 else .5
        ax=anchor((bounds[0]+bounds[2])/2-left,cw);ay=anchor((bounds[1]+bounds[3])/2-top,ch)
        for i in indices:
            e=elements[i]
            fitted[i]["pos"]=(round(margin+(e["pos"][0]-left)*scale+extra_x*ax),round(margin+(e["pos"][1]-top)*scale+extra_y*ay))
            fitted[i]["size"]=tuple(max(1,round(v*scale)) for v in e["size"])
            x,y=fitted[i]["pos"];w,h=fitted[i]["size"]
            if x<0 or y<0 or x+w>width or y+h>height:raise TelemetryError("Scene does not fit the video frame")
    return fitted,scale


class MapTransform:
    def __init__(self, points, size, padding=20):
        xs,ys=zip(*points);self.minx,self.maxy=min(xs),max(ys)
        self.scale=min((size[0]-2*padding)/max(1,max(xs)-min(xs)),(size[1]-2*padding)/max(1,max(ys)-min(ys)))
        self.offset=((size[0]-(max(xs)-min(xs))*self.scale)/2,(size[1]-(max(ys)-min(ys))*self.scale)/2)
    def point(self, p):return (self.offset[0]+(p[0]-self.minx)*self.scale,self.offset[1]+(self.maxy-p[1])*self.scale)


class FullSceneRenderer(FourChannelRenderer):
    def __init__(self, video, matches, width, height, scene):
        self.video,self.scene=video,scene
        self.timeline=Timeline(matches,scene.bindings)  # required channel/unit and ambiguity checks
        self.matches=matches
        self.sessions={m.vbo.path:Session(m.vbo) for m in matches}
        supported={"vbox.speed_gnd_mps", "vbox.lat_acc_smth_mps2", "vbox.lng_acc_smth_mps2",
                   "oled.plt_delta_t_ms", "laptiming.lap_time_ms", "lapresults.lap.session_best.lap_time_ms",
                   "lapresults.lap.previous.lap_time_ms", "lapresults.lap.previous.lap_number",
                   "vbox.local_ms", "system.day", "system.month", "system.year"}
        for element in scene.elements:
            for field in ("source", "xsource", "ysource"):
                if field not in element:continue
                source=element[field][0]
                can=re.fullmatch(r"can\.bin\[\d+\]\.channel\(([^)]+)\)",source)
                if can:
                    for session in self.sessions.values():
                        if can[1].lower() not in session.indices:
                            raise TelemetryError(f"{session.vbo.path.name}: scene channel {can[1]} is missing")
                elif source not in supported:
                    raise TelemetryError(f"Unsupported scene data source in {element['name']}: {source}")
        self.size=(width,height);self.position=(0,0)
        self.elements,self.scale=fit_layout(scene.elements,scene.size,width,height)
        self.blank=Image.new("RGBA",self.size)
        self.static=self.blank.copy()
        self.map_cache={};self.text_cache={}
        for e in self.elements:
            if e.get("image") is not None:
                e["image"]=e["image"].resize(e["size"],Image.Resampling.LANCZOS)
            if e.get("marker") is not None:
                e["marker"]=e["marker"].resize(tuple(max(3,round(v*self.scale)) for v in e["marker"].size),Image.Resampling.LANCZOS)
            if e["kind"] in ("image","bar","xyplot"):
                self.static.alpha_composite(e["image"],e["pos"])
        self.notes=list(scene.options["notes"])
        for session in self.sessions.values():
            if len(session.crossings)<2:self.notes.append(f"{session.vbo.path.name}: insufficient start-line crossings for complete lap timing; unavailable readings show dashes.")
            for e in self.elements:
                if e["kind"]!="map":continue
                transform=MapTransform(session.map_points,e["size"],max(6,round(20*self.scale)))
                im=Image.new("RGBA",e["size"]);d=ImageDraw.Draw(im);points=[transform.point(p) for p in session.map_points]
                if len(points)>1:
                    d.line(points,fill=(5,20,30,210),width=max(4,round(10*self.scale)),joint="curve")
                    d.line(points,fill=(240,249,255,255),width=max(2,round(4*self.scale)),joint="curve")
                self.map_cache[(session.vbo.path,e["name"])]=(im,transform)

    def source_value(self, source, session, utc, row, lap):
        key,scale=source
        can=re.fullmatch(r"can\.bin\[\d+\]\.channel\(([^)]+)\)",key)
        if can:value=row.get(can[1].lower())
        elif key=="vbox.speed_gnd_mps":value=row.get("velocity",0)/3.6
        elif key=="vbox.lat_acc_smth_mps2":value=session.acceleration(utc,True)
        elif key=="vbox.lng_acc_smth_mps2":value=session.acceleration(utc)
        elif key=="oled.plt_delta_t_ms":value=lap["delta"]*1000 if lap["delta"] is not None else None
        elif key in ("laptiming.lap_time_ms","lapresults.lap.session_best.lap_time_ms","lapresults.lap.previous.lap_time_ms"):
            field={"laptiming.lap_time_ms":"lap_time","lapresults.lap.session_best.lap_time_ms":"best_lap","lapresults.lap.previous.lap_time_ms":"previous_lap"}[key]
            value=lap[field]*1000 if lap[field] is not None else None
        elif key=="lapresults.lap.previous.lap_number":value=lap["lap_number"]
        elif key=="vbox.local_ms":value=((utc+self.scene.options["utc_offset"])%86400)*1000
        elif key in ("system.day","system.month","system.year"):
            value=getattr(datetime.fromtimestamp(utc,timezone(timedelta(seconds=self.scene.options["utc_offset"]))),key.split(".")[1])
        else:value=None
        return value*scale if value is not None else None

    def text_image(self,e,value):
        if e["fmtfn"]=="valid_time_ms":
            cs=max(0,round(value/10)) if value is not None else None
            text=f"{cs//6000:02d}:{cs//100%60:02d}.{cs%100:02d}" if cs is not None else "--:--.--"
        elif e["fmtfn"]=="time_hm":
            text=f"{int(value)//3600000%24:02d}:{int(value)//60000%60:02d}" if value is not None else "--:--"
        else:text=scene_number(e["fmt"],value)
        cache=self.text_cache.get(e["name"])
        if cache and cache[0]==text:return cache[1]
        glyphs=[e["font"].get(c,e["font"].get("-")) for c in text]
        if any(g is None for g in glyphs):raise TelemetryError("Scene font is missing required characters")
        tw=sum(g.width for g in glyphs);th=max(g.height for g in glyphs)
        raw=Image.new("RGBA",(tw,th));x=0
        for glyph in glyphs:raw.alpha_composite(glyph,(x,0));x+=glyph.width
        # Fit long lap times/numbers without truncating a digit.
        scale=min(self.scale,e["size"][0]/max(1,tw),e["size"][1]/max(1,th))
        raw=raw.resize((max(1,round(tw*scale)),max(1,round(th*scale))),Image.Resampling.LANCZOS)
        # A fine dark outline keeps the scene's white digits readable over sky.
        alpha=raw.getchannel("A").filter(ImageFilter.MaxFilter(3))
        outline=Image.new("RGBA",raw.size,(0,0,0,0));outline.putalpha(alpha)
        outline.alpha_composite(raw);raw=outline
        box=Image.new("RGBA",e["size"])
        x=e["size"][0]-raw.width if "right" in e["justification"] else (e["size"][0]-raw.width)//2
        box.alpha_composite(raw,(max(0,x),max(0,(e["size"][1]-raw.height)//2)))
        self.text_cache[e["name"]]=(text,box);return box

    def frame(self,seconds):
        utc=self.video.clock.utc(seconds)
        if self.timeline.at(utc) is None:return self.blank
        match=next((m for m in self.matches if m.rows[0].utc<=utc<=m.rows[-1].utc),None)
        if match is None:return self.blank
        session=self.sessions[match.vbo.path]
        i=bisect_left(session.times,utc)
        a=max(0,i-1);b=min(i,len(session.times)-1)
        f=(utc-session.times[a])/(session.times[b]-session.times[a]) if a!=b else 0
        row={c:float(session.vbo.rows[a].values[j])*(1-f)+float(session.vbo.rows[b].values[j])*f for c,j in session.indices.items()}
        lap=session.lap_values(utc)
        im=self.static.copy();draw=ImageDraw.Draw(im)
        for e in self.elements:
            x,y=e["pos"]
            if e["kind"]=="text":
                im.alpha_composite(self.text_image(e,self.source_value(e["source"],session,utc,row,lap)),(x,y))
            elif e["kind"]=="bar":
                value=self.source_value(e["source"],session,utc,row,lap)
                if value is None:continue
                for lo,hi,w,h,bx,by,colour,mode in e["bars"]:
                    w,h,bx,by=(round(v*self.scale) for v in (w,h,bx,by))
                    fill=round((h if mode=="bottom_top_min" else w)*max(0,min(1,(value-lo)/(hi-lo))))
                    if fill:draw.rectangle((x+bx,y+by+h-fill,x+bx+w-1,y+by+h-1) if mode=="bottom_top_min" else (x+bx,y+by,x+bx+fill-1,y+by+h-1),fill=colour)
            elif e["kind"]=="xyplot":
                vx=self.source_value(e["xsource"],session,utc,row,lap);vy=self.source_value(e["ysource"],session,utc,row,lap)
                if vx is None or vy is None:continue
                marker=e["marker"];w,h=e["size"]
                fx=max(0,min(1,(vx-e["xr"][0])/(e["xr"][1]-e["xr"][0])))
                fy=max(0,min(1,(vy-e["yr"][0])/(e["yr"][1]-e["yr"][0])))
                im.alpha_composite(marker,(x+round(fx*(w-marker.width)),y+round((1-fy)*(h-marker.height))))
            elif e["kind"]=="map":
                background,transform=self.map_cache[(session.vbo.path,e["name"])]
                im.alpha_composite(background,(x,y));px,py=transform.point(session.position(utc))
                marker=e["marker"]
                if marker is not None:
                    mx=round(px-marker.width/2);my=round(py-marker.height/2)
                    if 0<=mx<=e["size"][0]-marker.width and 0<=my<=e["size"][1]-marker.height:im.alpha_composite(marker,(x+mx,y+my))
        return im
