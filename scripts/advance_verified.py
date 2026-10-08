#!/usr/bin/env python3
"""Advance the bundled deterministic state after externally verified results."""
from pathlib import Path
import argparse, json, sys
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
from offline_manual_runner import advance, write_json, seal_manual_state

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--state',required=True, type=Path)
    ap.add_argument('--input',required=True, type=Path)
    ap.add_argument('--state-output',required=True, type=Path)
    ap.add_argument('--forecast-output',required=True, type=Path)
    ap.add_argument('--source-url',action='append',default=[])
    args=ap.parse_args()
    state=json.loads(args.state.read_text(encoding='utf-8'))
    payload=json.loads(args.input.read_text(encoding='utf-8'))
    config=ROOT/'zw_ssq_v1_config.json'; calendar=ROOT/'offline_calendar/zw_daily_calendar_2026_2099.jsonl'
    new_state, forecast=advance(state,payload,calendar,json.loads(config.read_text(encoding='utf-8')))
    # advance() validates and computes with the same frozen core. This wrapper
    # changes only provenance metadata because the result was web-verified.
    new_state['calculation_window'][0]['entry_assurance']='source_verified_public_web'
    new_state['data_assurance']='公开网页双来源核验结果；冻结配置、日历和算法未改变。'
    if args.source_url:
        new_state['source_urls']=args.source_url
        forecast['source_urls']=args.source_url
    new_state=seal_manual_state(new_state)
    forecast['data_assurance']='source_verified_public_web'
    write_json(args.state_output,new_state); write_json(args.forecast_output,forecast)
    print(json.dumps({'target':forecast['target'],'baseline':forecast['baseline'],'residual9_corrected':forecast['residual9_corrected'],'window':forecast['window'],'state_output':str(args.state_output),'forecast_output':str(args.forecast_output)},ensure_ascii=False,indent=2))
if __name__=='__main__': main()
