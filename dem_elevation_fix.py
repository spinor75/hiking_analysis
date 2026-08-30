#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GPX 고도 재계산 스크립트 (DEM 기반)
================================================
GPS 기압고도계의 노이즈를 제거하기 위해, GPX의 위경도 좌표를
외부 고도 데이터(DEM)로 다시 조회하여 정확한 고도 프로파일을 생성합니다.

[사용 전 준비]
  pip install gpxpy requests

[실행]
  python dem_elevation_fix.py 파일명.gpx

[출력]
  - 원본거리, 보정거리
  - DEM 기반 누적상승/하강
  - 거리-고도 프로파일 CSV (파일명_profile.csv)
  - 구간별 경사 분석

[고도 소스]
  기본: OpenTopoData 공개 API (한국은 SRTM 30m).
  더 정확한 한국 5m DEM을 원하면 국토지리정보원 데이터를 받아
  USE_LOCAL_DEM 옵션을 쓰세요 (하단 주석 참고).
"""

import sys
import time
import math
import csv
import gpxpy
import requests

# ============ 설정 ============
# OpenTopoData 데이터셋 선택:
#   'srtm30m'  - 전세계 30m (기본, 무료 공개서버)
#   'aster30m' - 전세계 30m 대안
# 한국 고해상도가 필요하면 자체 호스팅 또는 국토지리원 DEM 사용 (하단 참고)
DATASET = 'srtm30m'
API_URL = f'https://api.opentopodata.org/v1/{DATASET}'
BATCH = 100          # 한 번에 조회할 좌표 수 (공개서버 제한 100)
SLEEP = 1.0          # 배치 간 대기(초). 공개서버 부하 배려 (1초당 1요청 제한)
DOWNSAMPLE_SEC = 30  # GPX 다운샘플 간격(초). 노이즈 완화 + 요청 수 절감


def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2 * R * math.asin(math.sqrt(a))


def load_points(path):
    with open(path, encoding='utf-8') as f:
        gpx = gpxpy.parse(f)
    pts = []
    for trk in gpx.tracks:
        for seg in trk.segments:
            for p in seg.points:
                pts.append({'lat': p.latitude, 'lon': p.longitude,
                            'ele_gps': p.elevation, 'time': p.time})
    return pts


def downsample(pts, sec):
    if not pts or pts[0]['time'] is None:
        # 시간 정보 없으면 인덱스 기준 10개당 1개
        return pts[::10]
    out = [pts[0]]
    for p in pts[1:]:
        if (p['time'] - out[-1]['time']).total_seconds() >= sec:
            out.append(p)
    return out


def fetch_dem_elevations(pts):
    """OpenTopoData API로 고도 일괄 조회."""
    eles = []
    for i in range(0, len(pts), BATCH):
        chunk = pts[i:i+BATCH]
        locs = '|'.join(f"{p['lat']},{p['lon']}" for p in chunk)
        for attempt in range(3):
            try:
                r = requests.post(API_URL, data={'locations': locs}, timeout=60)
                r.raise_for_status()
                results = r.json()['results']
                eles.extend(x['elevation'] for x in results)
                break
            except Exception as e:
                print(f"  배치 {i//BATCH+1} 재시도 {attempt+1}/3: {e}")
                time.sleep(3)
        else:
            raise RuntimeError("API 조회 실패. 네트워크나 서버 상태를 확인하세요.")
        print(f"  진행: {min(i+BATCH, len(pts))}/{len(pts)} 좌표 완료")
        time.sleep(SLEEP)
    return eles


def smooth(vals, w=2):
    out = []
    for i in range(len(vals)):
        a, b = max(0, i-w), min(len(vals), i+w+1)
        seg = [v for v in vals[a:b] if v is not None]
        out.append(sum(seg)/len(seg) if seg else vals[i])
    return out


def analyze(path):
    print(f"\n=== {path} ===")
    pts = load_points(path)
    print(f"원본 포인트: {len(pts)}")

    s = downsample(pts, DOWNSAMPLE_SEC)
    print(f"다운샘플({DOWNSAMPLE_SEC}s): {len(s)} 포인트")

    # 누적 거리 (다운샘플 기준)
    cum = [0.0]
    for i in range(1, len(s)):
        cum.append(cum[-1] + haversine(s[i-1]['lat'], s[i-1]['lon'],
                                       s[i]['lat'], s[i]['lon']))

    print("DEM 고도 조회 중...")
    dem = fetch_dem_elevations(s)
    dem_s = smooth(dem, 2)

    # 누적 상승/하강 (DEM 기준)
    asc = sum(max(0, dem_s[i]-dem_s[i-1]) for i in range(1, len(dem_s)))
    desc = sum(max(0, dem_s[i-1]-dem_s[i]) for i in range(1, len(dem_s)))

    # GPS 기압고도 기준 (비교용)
    gps_e = [p['ele_gps'] for p in s if p['ele_gps'] is not None]
    gps_asc = None
    if len(gps_e) == len(s):
        ge = smooth(gps_e, 2)
        gps_asc = sum(max(0, ge[i]-ge[i-1]) for i in range(1, len(ge)))

    print(f"\n총 거리: {cum[-1]/1000:.2f} km")
    print(f"DEM 누적상승: +{asc:.0f} m / 하강: -{desc:.0f} m")
    if gps_asc is not None:
        print(f"GPS 기압고도 누적상승: +{gps_asc:.0f} m (비교용)")
    print(f"DEM 최고/최저: {max(dem_s):.0f} m / {min(dem_s):.0f} m")

    # 거리-고도 프로파일 CSV
    out_csv = path.rsplit('.', 1)[0] + '_profile.csv'
    with open(out_csv, 'w', newline='', encoding='utf-8-sig') as f:
        wr = csv.writer(f)
        wr.writerow(['거리_km', 'DEM고도_m', 'GPS고도_m'])
        for i in range(len(s)):
            gps_val = s[i]['ele_gps'] if s[i]['ele_gps'] is not None else ''
            wr.writerow([f"{cum[i]/1000:.3f}", f"{dem_s[i]:.1f}", gps_val])
    print(f"\n프로파일 저장: {out_csv}")

    # 구간별 경사 (200m 거리 단위 재샘플 → m/km)
    print("\n[거리 200m 단위 경사 분포]")
    seg_grades = []
    target = 200
    last_i = 0
    for i in range(1, len(s)):
        if cum[i] - cum[last_i] >= target:
            d = cum[i] - cum[last_i]
            rise = dem_s[i] - dem_s[last_i]
            grade = rise / d * 1000  # m/km
            seg_grades.append(grade)
            last_i = i
    if seg_grades:
        steep = [g for g in seg_grades if g >= 200]
        print(f"  200m 구간 수: {len(seg_grades)}")
        print(f"  급경사(>=200m/km 상승) 구간: {len(steep)}개 "
              f"({len(steep)/len(seg_grades)*100:.1f}%)")
        print(f"  최대 경사: {max(seg_grades):.0f} m/km")
        print(f"  평균 상승경사: "
              f"{sum(g for g in seg_grades if g>0)/max(1,len([g for g in seg_grades if g>0])):.0f} m/km")


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("사용법: python dem_elevation_fix.py 파일1.gpx [파일2.gpx ...]")
        sys.exit(1)
    for path in sys.argv[1:]:
        try:
            analyze(path)
        except Exception as e:
            print(f"오류 ({path}): {e}")

# ============================================================
# [한국 고해상도 5m DEM 사용법 - 선택]
# 국토지리정보원 국토정보플랫폼(map.ngii.go.kr)에서
# 수치표고모델(DEM) 5m 또는 30m 격자를 GeoTIFF로 내려받은 뒤,
# rasterio로 로컬 조회하면 API 없이 더 정확합니다.
#
#   pip install rasterio pyproj
#
#   import rasterio
#   from pyproj import Transformer
#   ds = rasterio.open('korea_dem.tif')
#   # DEM이 보통 EPSG:5186(중부원점TM) 등이므로 좌표변환 필요
#   tf = Transformer.from_crs('EPSG:4326', ds.crs, always_xy=True)
#   def get_ele(lat, lon):
#       x, y = tf.transform(lon, lat)
#       return next(ds.sample([(x, y)]))[0]
#
# 이 방식은 인터넷 없이 동작하고 한국 산악 지형에서 가장 정확합니다.
# ============================================================
