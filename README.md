# hiking_analysis

GPX 등산 기록을 정밀 분석하기 위한 파이썬 스크립트 모음입니다. GPS 기기의 기압고도계 노이즈를 DEM(수치표고모델) 기반으로 보정하고, 산행 시간대의 실제 기상(기온·습도·체감온도)을 매칭하여 분석합니다.

## 스크립트 구성

### 1. `dem_elevation_fix.py` — GPX 고도 재계산

GPS 기압고도계는 기압 변화·센서 노이즈로 오차가 큽니다. 이 스크립트는 GPX의 위경도 좌표를 외부 DEM 데이터로 다시 조회하여 정확한 고도 프로파일을 만듭니다.

**주요 기능**
- GPX 트랙 포인트 로드 및 시간 간격(기본 30초) 기준 다운샘플링
- [OpenTopoData](https://www.opentopodata.org/) 공개 API(SRTM 30m)를 통한 좌표별 고도 일괄 조회 (배치 100개, 요청 간 1초 대기, 실패 시 재시도)
- 이동평균 스무딩 후 DEM 기준 누적 상승/하강 계산, GPS 기압고도 값과 비교 출력
- 거리-고도 프로파일을 `파일명_profile.csv`로 저장
- 200m 구간 단위 경사(m/km) 분포 및 급경사 구간 비율 분석
- (선택) 국토지리정보원 5m/30m 국내 DEM(GeoTIFF)을 `rasterio`로 로컬 조회해 오프라인·고정밀 분석하는 방법을 코드 하단 주석에 안내

**사용법**
```bash
pip install gpxpy requests
python dem_elevation_fix.py 파일명.gpx [파일2.gpx ...]
```

**출력**
- 콘솔: 원본/보정 거리, DEM 기준 누적상승·하강, 최고/최저 고도, 경사 분포
- 파일: `파일명_profile.csv` (거리_km, DEM고도_m, GPS고도_m)

### 2. `weather_fetch.py` — 산행 시간대 기온·습도 분석

GPX의 이동 시간대에 대응하는 기상청 시간자료(ASOS)를 받아, 관측소 해발고도와 산행 고도 차이를 감률(0.0065℃/m)로 보정한 뒤 체감온도까지 계산합니다.

**주요 기능**
- 파일명에 포함된 산 이름 키워드(`STATION_HINT`)로 가장 가까운 ASOS 관측소를 자동 선택 (매칭 없으면 서울 108 기본값)
- 기상청 API 허브(`kma_sfctm3`)에서 해당 날짜의 시간별 기온·습도 조회
- 이동 구간만 필터링(60초 다운샘플, 속도 1km/h 이상)하여 정지 시간 제외
- 관측소 해발고도 대비 산행 고도 차이를 기온 감률로 보정 (`ta_corr`)
- 보정 기온·습도로 간이 Heat Index(체감온도) 계산
- 여러 GPX를 한꺼번에 처리해 `weather_summary.csv`로 요약 저장
- **증분 처리**: 실행 폴더에 기존 `weather_summary.csv`가 있으면 이미 기록된 파일명은 건너뛰고, 새로 발견된 GPX만 기상청 API를 호출해 기존 CSV에 이어붙임 (불필요한 재조회 방지)

**사용법**
```bash
pip install gpxpy requests
# https://apihub.kma.go.kr 가입 후 인증키 발급, 스크립트 상단 API_KEY에 입력
python weather_fetch.py 20240726_055855.gpx
python weather_fetch.py *.gpx
```

**출력**
- 콘솔: 산행별 관측소 평균기온, 고도보정 평균기온, 평균습도, 평균 체감온도
- 파일: `weather_summary.csv` (file, date, station, ta_station, ta_corr, rh, hi)

### 3. `merge_gpx.py` — GPX 파일 병합

하나의 등산 기록이 GPS 기기 오류나 재시작 등으로 여러 개의 GPX 파일로 나뉘어 저장된 경우, 이를 하나의 GPX 파일로 합칩니다.

**주요 기능**
- 각 GPX 파일 트랙의 시작 시각을 기준으로 정렬 후 병합 (`--no-sort`로 입력 순서 그대로 병합 가능)
- 웨이포인트, 루트도 함께 병합
- 출력 파일명은 `-o` 옵션으로 지정

**사용법**
```bash
pip install gpxpy
python merge_gpx.py 파일1.gpx 파일2.gpx [파일3.gpx ...] -o 합친결과.gpx
```

**출력**
- 콘솔: 합친 파일 수, 총 포인트 수, 트랙 수
- 파일: `-o`로 지정한 GPX 파일

## 참고 / 알려진 제한

- `dem_elevation_fix.py`: 기본 SRTM 30m은 정밀도가 제한적이며, 국내 산악지형은 국토지리정보원 DEM(5m)을 로컬로 쓰는 편이 더 정확합니다.
- `weather_fetch.py`: 고도 보정 공식은 단순화된 버전입니다. `STATION_ELEV`에 없는 관측소를 쓸 경우 기본값(50m)이 적용되므로 필요 시 직접 추가하세요. 또한 기상청 API 응답의 기온(`ta`)·습도(`hm`) 컬럼 인덱스는 `help=1` 옵션으로 실제 헤더를 확인 후 조정이 필요할 수 있습니다. 증분 처리는 `weather_summary.csv`의 `file` 컬럼 값(스크립트 실행 시 넘긴 경로 문자열)을 기준으로 비교하므로, 이전 실행과 다른 상대/절대 경로로 같은 파일을 지정하면 중복으로 다시 조회될 수 있습니다.

## 요구 사항

- Python 3.x
- `gpxpy`, `requests`
