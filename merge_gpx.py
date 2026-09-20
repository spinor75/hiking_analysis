#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GPX 파일 병합 스크립트
================================================
하나의 등산 기록이 GPS 기기 오류나 재시작 등으로 여러 개의 GPX 파일로
나뉘어 저장된 경우, 이를 하나의 GPX 파일로 합칩니다.

[사용 전 준비]
  pip install gpxpy

[실행]
  python merge_gpx.py 파일1.gpx 파일2.gpx [파일3.gpx ...] -o 합친결과.gpx

[동작]
  - 각 GPX 파일의 트랙(트랙포인트) 시작 시각을 기준으로 정렬한 뒤 순서대로 합침
    (--no-sort 옵션을 주면 입력한 순서 그대로 합침)
  - 웨이포인트, 루트도 함께 병합
"""

import argparse

import gpxpy
import gpxpy.gpx


def load_gpx(path):
    with open(path, encoding='utf-8') as f:
        return gpxpy.parse(f)


def track_start_time(gpx):
    """트랙의 가장 이른 포인트 시각을 반환합니다 (시각 정보가 없으면 None)."""
    times = [p.time for trk in gpx.tracks for seg in trk.segments for p in seg.points if p.time]
    return min(times) if times else None


def merge_gpx_files(input_paths, sort_by_time=True):
    gpxs = [(path, load_gpx(path)) for path in input_paths]

    if sort_by_time:
        gpxs.sort(key=lambda item: (track_start_time(item[1]) is None, track_start_time(item[1]) or item[0]))

    merged = gpxpy.gpx.GPX()
    for _, gpx in gpxs:
        merged.waypoints.extend(gpx.waypoints)
        merged.routes.extend(gpx.routes)
        merged.tracks.extend(gpx.tracks)

    return merged


def main():
    parser = argparse.ArgumentParser(description='여러 GPX 파일을 하나로 합칩니다.')
    parser.add_argument('inputs', nargs='+', help='합칠 GPX 파일들')
    parser.add_argument('-o', '--output', required=True, help='결과를 저장할 GPX 파일 이름')
    parser.add_argument('--no-sort', action='store_true',
                         help='입력한 순서 그대로 합침 (기본값: 트랙 시작 시각 기준 정렬)')
    args = parser.parse_args()

    merged = merge_gpx_files(args.inputs, sort_by_time=not args.no_sort)

    with open(args.output, 'w', encoding='utf-8') as f:
        f.write(merged.to_xml())

    total_points = sum(len(seg.points) for trk in merged.tracks for seg in trk.segments)
    print(f"{len(args.inputs)}개의 GPX 파일을 합쳤습니다 -> {args.output} "
          f"(포인트 {total_points}개, 트랙 {len(merged.tracks)}개)")


if __name__ == '__main__':
    main()
