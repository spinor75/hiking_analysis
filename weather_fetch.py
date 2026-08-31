#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
산행 기온·습도 분석 스크립트 (기상청 API 기반)
==================================================
GPX의 산행 시간대 동안의 시간별 기온·습도를 기상청에서 받아,
고도 보정을 거쳐 "그 시각 그 고도에서 실제로 느낀 기온"을 계산하고
이동 시간대 평균 기온/체감온도를 산출합니다.

[준비]
  pip install gpxpy requests
  기상청 API 허브(https://apihub.kma.go.kr) 가입 → 무료 인증키 발급
  아래 API_KEY에 붙여넣기

[실행]
  python weather_fetch.py 20240726_055855.gpx
  python weather_fetch.py *.gpx          # 여러 개 한꺼번에

[출력]
  - 산행별 이동시간대 평균기온, 평균습도, 평균 체감온도(고도보정)
  - weather_summary.csv (전체 산행 요약 - FI 비교용)
  - 산행별 시간-기온 상세 CSV

[재실행 시]
  같은 폴더에 weather_summary.csv가 이미 있으면, 그 파일에 기록된
  산행(파일명 기준)은 건너뛰고 새로 발견된 산행 기록만 기상청에서
  받아와 기존 CSV에 이어붙입니다.

[데이터 소스]
  기상청 ASOS(종관관측, 시간자료) 또는 AWS(방재관측, 매분자료→시간평균)
  API: 지상 시간자료 (kma_sfctm)
"""

import sys, time, math, csv, glob, os
from datetime import timezone, timedelta
import requests
import gpxpy

# ====== 설정 ======
API_KEY = "여기에_기상청_API허브_인증키_붙여넣기"   # <<< 발급받은 키 입력

# 산행 위치별 가장 가까운 ASOS 관측소 지점번호 (stnId)
# 기본은 자동 선택을 시도하되, 매핑이 있으면 우선 사용
# 주요 관측소: 서울108, 수원119, 北春천101, 충주127, 제천221, 영주(풍기)272, 보은226, 속초90, 대관령100
STATION_HINT = {
    '관악': 119,   # 수원 (관악산 남측)
    '북한산': 108, # 서울
    '도봉': 108,   # 서울
    '의상': 108,   # 서울
    '비봉': 108,   # 서울
    '백운동': 108, # 서울
    '광교': 119,   # 수원
    '청계': 119,   # 수원/광청
    '한계령': 90,  # 속초 (설악산)
    '설악': 90,    # 속초
    '오색': 90,    # 속초
    '대청': 90,    # 속초
    '봉정': 90,    # 속초
    '백담': 90,    # 속초
    '오대': 100,   # 대관령
    '소백': 272,   # 영주(풍기)
    '어의곡': 272,
    '천동': 221,   # 제천
    '속리': 226,   # 보은
    '문장대': 226,
}
DEFAULT_STATION = 108  # 서울

# 관측소 해발고도(m) - 고도보정 기준
STATION_ELEV = {108:86, 119:34, 90:18, 100:772, 272:211, 221:264, 226:175, 101:77, 127:116}
DEFAULT_ELEV = 50

LAPSE = 0.0065  # 기온 감률 ℃/m (고도 100m당 0.65℃)


def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2-lat1); dl = math.radians(lon2-lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*R*math.asin(math.sqrt(a))


def load_existing_summary(csv_path):
    """이전에 저장된 weather_summary.csv를 읽어 {파일명: 행딕셔너리}로 반환.
    파일이 없으면 빈 딕셔너리."""
    existing = {}
    if not os.path.exists(csv_path):
        return existing
    with open(csv_path, encoding='utf-8-sig', newline='') as f:
        for row in csv.DictReader(f):
            existing[row['file']] = row
    return existing


def pick_station(path):
    for key, stn in STATION_HINT.items():
        if key in path:
            return stn
    return DEFAULT_STATION


def load_gpx(path):
    with open(path, encoding='utf-8') as f:
        gpx = gpxpy.parse(f)
    pts = []
    for trk in gpx.tracks:
        for seg in trk.segments:
            for p in seg.points:
                pts.append({'lat':p.latitude,'lon':p.longitude,
                            'ele':p.elevation,'time':p.time})
    return pts


def heat_index(T, RH):
    """체감온도(섭씨). 간이 Heat Index (T>=27, RH>=40 구간에서 의미)."""
    if T < 20 or RH is None:
        return T
    Tf = T*9/5+32
    HI = (-42.379 + 2.04901523*Tf + 10.14333127*RH - 0.22475541*Tf*RH
          - 0.00683783*Tf*Tf - 0.05481717*RH*RH + 0.00122874*Tf*Tf*RH
          + 0.00085282*Tf*RH*RH - 0.00000199*Tf*Tf*RH*RH)
    return round((HI-32)*5/9, 1)


def fetch_kma_hourly(stn, ymd_start, ymd_end):
    """
    기상청 API 허브 지상 시간자료 조회.
    반환: {datetime_str 'YYYYMMDDHH': {'ta':기온, 'hm':습도}}
    """
    url = "https://apihub.kma.go.kr/api/typ01/url/kma_sfctm3.php"
    params = {
        'tm1': ymd_start + '0000',
        'tm2': ymd_end + '2300',
        'stn': stn,
        'help': 0,
        'authKey': API_KEY,
    }
    r = requests.get(url, params=params, timeout=60)
    r.raise_for_status()
    text = r.text
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        cols = line.split()
        if len(cols) < 14:
            continue
        try:
            tm = cols[0]            # YYYYMMDDHHMI
            ta = float(cols[11])    # 기온 (컬럼 위치는 포맷에 따라 조정 필요)
            hm = float(cols[13])    # 습도
            hh = tm[:10]
            if ta > -50:
                out[hh] = {'ta':ta, 'hm':hm if hm>0 else None}
        except (ValueError, IndexError):
            continue
    return out


def analyze(path):
    print(f"\n=== {path} ===")
    pts = load_gpx(path)
    if not pts or pts[0]['time'] is None:
        print("  시간 정보 없음, 건너뜀")
        return None

    kst = timezone(timedelta(hours=9))
    t_start = pts[0]['time'].astimezone(kst)
    t_end = pts[-1]['time'].astimezone(kst)
    ymd = t_start.strftime('%Y%m%d')
    stn = pick_station(path)
    print(f"  산행: {t_start:%Y-%m-%d %H:%M} ~ {t_end:%H:%M}, 관측소 {stn}")

    # 이동 구간 판정 (60초 다운샘플 + 1km/h 이상)
    s = [pts[0]]
    for p in pts[1:]:
        if (p['time']-s[-1]['time']).total_seconds() >= 60:
            s.append(p)

    # 기상 데이터
    try:
        wx = fetch_kma_hourly(stn, ymd, ymd)
    except Exception as e:
        print(f"  기상 조회 실패: {e}")
        return None
    if not wx:
        print("  기상 데이터 없음 (관측소/날짜 확인)")
        return None

    # 각 이동 포인트에 시간별 기온 매칭 + 고도 보정
    samples = []
    for i in range(1, len(s)):
        d = haversine(s[i-1]['lat'],s[i-1]['lon'],s[i]['lat'],s[i]['lon'])
        dt = (s[i]['time']-s[i-1]['time']).total_seconds()
        if dt<=0: continue
        spd = (d/1000)/(dt/3600)
        if spd < 1.0:   # 정지 제외
            continue
        tm = s[i]['time'].astimezone(kst)
        hh = tm.strftime('%Y%m%d%H')
        if hh not in wx:
            continue
        ta_station = wx[hh]['ta']
        rh = wx[hh]['hm']
        # 고도 보정: 관측소는 저지대, 산행 고도와의 차이만큼 기온 보정
        # (관측소 해발은 단순화를 위해 무시, 산행 고도 자체에 감률 적용은
        #  과대보정이므로, 관측소 해발 대비 상대고도가 필요. 여기선 보수적으로
        #  GPX고도 기준 700m 초과분에만 감률 적용 - 사용자 환경서 관측소 해발로 교체 권장)
        ele = s[i]['ele'] or 0
        stn_elev = STATION_ELEV.get(stn, DEFAULT_ELEV)
        ta_corr = ta_station - (ele - stn_elev) * LAPSE  # 관측소 해발 대비 상대고도 보정
        hi = heat_index(ta_corr, rh)
        samples.append({'ta_station':ta_station,'ta_corr':ta_corr,'rh':rh,'hi':hi})

    if not samples:
        print("  매칭된 샘플 없음")
        return None

    avg_station = sum(x['ta_station'] for x in samples)/len(samples)
    avg_corr = sum(x['ta_corr'] for x in samples)/len(samples)
    rhs = [x['rh'] for x in samples if x['rh']]
    avg_rh = sum(rhs)/len(rhs) if rhs else None
    his = [x['hi'] for x in samples]
    avg_hi = sum(his)/len(his)

    print(f"  관측소 평균기온: {avg_station:.1f}℃")
    print(f"  고도보정 평균기온: {avg_corr:.1f}℃")
    print(f"  평균습도: {avg_rh:.0f}%" if avg_rh else "  습도 없음")
    print(f"  평균 체감온도: {avg_hi:.1f}℃")

    return {'file':path,'date':ymd,'station':stn,
            'ta_station':round(avg_station,1),'ta_corr':round(avg_corr,1),
            'rh':round(avg_rh) if avg_rh else '','hi':round(avg_hi,1)}


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("사용법: python weather_fetch.py 파일1.gpx [파일2.gpx ...]")
        sys.exit(1)
    if API_KEY.startswith("여기에"):
        print("⚠️  먼저 스크립트 상단 API_KEY에 기상청 API허브 인증키를 입력하세요.")
        print("   발급: https://apihub.kma.go.kr 가입 → 마이페이지 → 인증키")
        sys.exit(1)

    SUMMARY_CSV = 'weather_summary.csv'
    results = load_existing_summary(SUMMARY_CSV)
    if results:
        print(f"기존 {SUMMARY_CSV} 발견: {len(results)}개 산행 기록은 건너뜁니다.")

    new_count = 0
    for pattern in sys.argv[1:]:
        for path in sorted(glob.glob(pattern)):
            if path in results:
                print(f"건너뜀 (이미 처리됨): {path}")
                continue
            try:
                r = analyze(path)
                if r:
                    results[path] = r
                    new_count += 1
                time.sleep(0.5)
            except Exception as e:
                print(f"오류 ({path}): {e}")

    if results:
        with open(SUMMARY_CSV, 'w', newline='', encoding='utf-8-sig') as f:
            wr = csv.DictWriter(f, fieldnames=['file','date','station','ta_station','ta_corr','rh','hi'])
            wr.writeheader()
            wr.writerows(results.values())
        print(f"\n요약 저장: {SUMMARY_CSV} (신규 {new_count}개 추가, 총 {len(results)}개 산행)")
    else:
        print("\n처리할 산행 기록이 없습니다.")

# ============================================================
# [중요 - 고도 보정에 관하여]
# 위 코드의 고도 보정(ta_corr)은 단순화된 버전입니다.
# 정확히 하려면 "관측소 해발고도"를 빼야 합니다:
#     ta_corr = ta_station - (산행고도 - 관측소해발) * 0.0065
# 관측소 해발(예: 서울 108 = 86m, 속초 90 = 18m, 대관령 100 = 772m)을
# STATION_ELEV 딕셔너리로 만들어 빼주면 정확합니다.
# 대관령(772m)처럼 높은 관측소는 보정을 거의 안 해도 됩니다.
#
# [API 컬럼 위치 주의]
# kma_sfctm3 응답의 기온(ta)·습도(hm) 컬럼 인덱스(현재 11, 13)는
# 실제 응답 헤더(help=1로 확인)에 맞춰 조정이 필요할 수 있습니다.
# 첫 실행 시 help=1로 한 번 호출해 컬럼 순서를 확인하세요.
# ============================================================
