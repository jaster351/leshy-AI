"""Spec-grounded AYCoCg recovery, chroma subsampling and malformed fields."""
import random
import pytest
from leshy.rdp6_planar import RDP6PlanarError, RDP6PlanarUnsupported, decompress_rdp6_planar


def raw(cll=1, width=1, height=1, *, y=None, co=None, cg=None, cs=False, alpha=None):
    n=width*height
    sub=((width+1)//2)*((height+1)//2) if cs else n
    yy=bytes([100]*n) if y is None else bytes(y)
    cc=bytes([0]*sub) if co is None else bytes(co)
    gg=bytes([0]*sub) if cg is None else bytes(cg)
    return bytes([cll | (8 if cs else 0) | (0x20 if alpha is None else 0)]) + (b'' if alpha is None else bytes(alpha)) + yy + cc + gg


def rgb_from(y,co,cg,cll):
    def signed(byte):
        value=(byte << (cll-1)) & 255
        return value-256 if value >=128 else value
    c=signed(co);g=signed(cg)
    return bytes([min(255,max(0,y-g-c)),min(255,max(0,y+g)),min(255,max(0,y-g+c))])


def test_gray_no_chroma_cll_all_levels():
    for cll in range(1,8):
        assert decompress_rdp6_planar(raw(cll),1,1) == b'\x64'*3


@pytest.mark.parametrize('cll,y,co,cg',[ (1,100,10,20),(2,100,5,2),(7,100,2,2),(1,255,50,50),(1,0,255,255), (4,190,10,5) ])
def test_ycocg_spec_signed_conversion(cll,y,co,cg):
    assert decompress_rdp6_planar(raw(cll,y=[y],co=[co],cg=[cg]),1,1)==rgb_from(y,co,cg,cll)


def test_bottom_up_correctly_flips_ycocg_planes():
    # stream pixel order is bottom row first, then top row
    wire=raw(1,2,2,y=[10,20,30,40],co=[0]*4,cg=[0]*4)
    assert decompress_rdp6_planar(wire,2,2) == bytes([30]*3+[40]*3+[10]*3+[20]*3)


def test_odd_3x3_chroma_subsampling_expands_2x2():
    # Chroma row0 affects bottom two source scanlines; chroma row1
    # affects final source scanline (top in image after vertical flip).
    wire=raw(1,3,3,cs=True,co=[10,20,30,40])
    frame=decompress_rdp6_planar(wire,3,3)
    expected_co=[30,30,40,10,10,20,10,10,20]
    assert frame == b''.join(rgb_from(100, co, 0, 1) for co in expected_co)


def test_even_2x2_chroma_has_single_chroma_sample():
    frame=decompress_rdp6_planar(raw(3,2,2,cs=True,co=[3],cg=[4]),2,2)
    assert frame == rgb_from(100,3,4,3)*4


def test_ycocg_opaque_alpha_accepted():
    assert decompress_rdp6_planar(raw(1,1,1,y=[100],alpha=[255]),1,1)==b'\x64'*3


def test_ycocg_translucent_alpha_rejected():
    with pytest.raises(RDP6PlanarUnsupported):
        decompress_rdp6_planar(raw(1,1,1,alpha=[254]),1,1)


def test_rgb_chroma_without_cll_rejected():
    with pytest.raises(RDP6PlanarError):
        decompress_rdp6_planar(raw(0,2,2,cs=True,co=[0],cg=[0]),2,2)


def test_short_odd_subsampled_chroma_rejected():
    wire=raw(1,3,3,cs=True)
    with pytest.raises(RDP6PlanarError):
        decompress_rdp6_planar(wire[:-1],3,3)


def test_rle_ycocg_and_subsampling():
    # First raw Y pixel 100 + 3 repeats. Both chroma planes 0.
    rle=bytes([0x39]) + b'\x20\x64\x64' + b'\x20\x00\x00' + b'\x10\x0a' + b'\x10\x00'
    assert decompress_rdp6_planar(rle,2,2) == rgb_from(100,10,0,1)*4


def test_odd_subsampled_rle_planes():
    # 3x3 Y source: first row 100; other rows repeat previous via delta=0.
    yy=b'\x30\x64\x64\x64' + b'\x30\x00\x00\x00'*2
    # chroma planes each 2x2: first scanline 2 pixels 10; next repeats.
    co=b'\x20\x0a\x0a' + b'\x20\x00\x00'
    cg=b'\x20\x00\x00' * 2
    got=decompress_rdp6_planar(bytes([0x39])+yy+co+cg,3,3)
    assert got==rgb_from(100,10,0,1)*9


def test_random_valid_ycocg_lossy_no_exception():
    rng=random.Random(640)
    for i in range(100):
        width=rng.randrange(1,8); height=rng.randrange(1,8)
        cll=rng.randrange(1,8); subsample=rng.choice([True,False])
        n=width*height; sub=((width+1)//2)*((height+1)//2) if subsample else n
        wire=raw(cll,width,height,cs=subsample,y=rng.randbytes(n),co=rng.randbytes(sub),cg=rng.randbytes(sub))
        result=decompress_rdp6_planar(wire,width,height)
        assert len(result)==n*3
