"""Visible-part evidence extraction; bbox overlap is not a visibility label."""
import cv2
import numpy as np


def regions(image,box,others,mask=None):
    h,w=image.shape[:2];x1,y1,x2,y2=map(int,box)
    x1,y1=max(0,x1),max(0,y1);x2,y2=min(w,x2),min(h,y2)
    if x2-x1<24 or y2-y1<48:return {},0.
    allowed=np.ones((h,w),np.uint8)
    if mask is None:
        for b in others:
            a,c,d,e=map(int,b);allowed[max(0,c):min(h,e),max(0,a):min(w,d)]=0
    else:
        if mask.shape!=(h,w):raise ValueError('Instance mask must match frame')
        allowed=(mask>.5).astype(np.uint8)
    crops={};area=0
    for row in range(3):
        for col in range(2):
            a=x1+(x2-x1)*col//2;b=x1+(x2-x1)*(col+1)//2
            c=y1+(y2-y1)*row//3;d=y1+(y2-y1)*(row+1)//3
            support=allowed[c:d,a:b]
            if support.size and support.mean()>=.95:
                crop=image[c:d,a:b].copy()
                if mask is not None:crop[support==0]=0
                crops[f'{row}:{col}']=crop;area+=(b-a)*(d-c)
    return crops,area/max(1,(x2-x1)*(y2-y1))
