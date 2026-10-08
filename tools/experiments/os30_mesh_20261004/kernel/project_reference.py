"""Exact RAW worker projection (viewport centerY/halfheight in Q4)."""
from reference import signed,word


def project(vertex, viewport):
    x,y,z,w,s,t,color=map(signed,vertex)
    cx,cy,hw,hh=map(signed,viewport)
    depth=(1<<38)//max(w,256);zi=depth>>6
    rx=signed((x*zi)>>16);ry=signed((y*zi)>>16)
    sx=max(-32768,min(32767,(cx<<4)+((hw*rx)>>12)))
    sy=max(-32768,min(32767,cy-((hh*ry)>>16)))
    rgb=((color>>3)&31)<<11|((color>>10)&63)<<5|((color>>19)&31)
    return [((sy&65535)<<16)|(sx&65535),word(s),word(t),zi,depth,
            (((color>>24)&255)<<16)|rgb,0]
