# 환동퀀트 — 통합 퀀트 대시보드

평일 아침 08:37(KST)에 GitHub Actions가 자동으로 실행된다. 전 거래일 종가 기준으로 계산하고, 결과를 대시보드(GitHub Pages)와 텔레그램으로 보낸다. 서버·비용 없음.

| 탭 | 내용 |
|---|---|
| 종합 | 밸류·퀄리티·모멘텀 백분위 평균 |
| A안 | 코스피 마법공식 상위 8 + 소형주 모멘텀 상위 7 (근사) |
| 마법공식 | 이익수익률 + ROC 순위 합 |
| 밸류 / 퀄리티 / 모멘텀 | 각 팩터 점수 |
| 매수신호 | 골든크로스·RSI·볼린저·스토캐스틱·신고가·거래량 (최근 3일) |
| 급등후보 | 거래량·돌파·밴드 수축 점수 (참고용) |
| 성과 | 각 전략 추천을 매일 기록 → 5·20·60거래일 뒤 수익률 vs 시장 평균 |
| 코인 | BTC·ETH·SOL·XRP 온체인 국면·현황·전략 비교·내 조합 전략 (아래 '코인 탭') |

## 설치 (한 번만)

### 1. API 키 발급 — ⚠️ 민감한 정보, 직접 입력
- **DART**: https://opendart.fss.or.kr → 인증키 신청 (즉시 발급)
- **KRX**: https://openapi.krx.co.kr → 회원가입 → 마이페이지 → API 인증키 신청 → 승인 후 **서비스이용 → 주식**에서 `유가증권 일별매매정보`, `코스닥 일별매매정보` 두 개 **API 이용신청** (승인까지 보통 하루 정도)
- **텔레그램(선택)**: @BotFather에서 봇 생성 → 토큰. 봇에게 아무 말이나 보낸 뒤 `https://api.telegram.org/bot<토큰>/getUpdates`에서 `chat.id` 확인

### 2. GitHub 저장소
- **Public** 저장소에 이 폴더 내용을 올린다 (무료 Pages는 Public만 가능. 키는 Secrets에 들어가므로 공개되지 않는다)
- Settings → Secrets and variables → Actions → New repository secret
  - `KRX_API_KEY`, `DART_API_KEY` (필수)
  - `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (선택)
- Settings → Pages → Source: **GitHub Actions**
- Actions 탭 → workflow 사용 허용 → `daily` → **Run workflow**
  - 첫 실행은 1년 치 주가를 받느라 5~10분, 이후는 1~2분

### 3. 폰에서 열기
`https://<깃허브아이디>.github.io/<저장소이름>/` → 브라우저 메뉴 → **홈 화면에 추가**

## 설정 바꾸기 — `config.json`
- `min_mcap_eok`: 최소 시가총액(억)
- `min_trdval20_eok`: 20일 평균 거래대금 하한(억)
- `aplan`: A안 종목 수, 소형주 기준(시총 하위 비율)

## 계산 방식과 한계
- **주가**: `KRX_API_KEY`가 있으면 KRX Open API(공식), 없으면 네이버 증권 시세(비공식, 수정주가)를 쓴다. 네이버는 주소가 바뀌면 멈출 수 있고 관리종목 필터가 없다.
- **재무**: 기본은 네이버 증권 분기 재무(최근 4분기 합 TTM, 자본 = BPS × 주식수, 부채 = 부채비율 × 자본). DART는 GitHub 서버(해외)에서 접속이 막혀 `config.json`의 `"fundamentals": "dart"`로 바꿔야만 쓴다(한국 서버에서 돌릴 때).
- **마법공식은 근사식**이다. 현금·차입금 자료가 없어서 EV는 `시총 + 부채총계`, 투하자본은 총자산(DART 사용 시 `유동자산 − 유동부채 + 비유동자산`)으로 계산한다.
- **A안 탭은 근사치**다. 기존 A안의 DART 제외 규칙은 아직 반영하지 않았다.
- **급등후보는 검증 전 점수**다. 성과 탭에 결과가 3개월 이상 쌓이기 전까지는 매매 근거로 쓰지 말 것.
- 데이터는 T-1(전 거래일) 종가 기준이다. 장중 실시간 신호가 아니다.

## 로컬 테스트
```bash
pip install -r requirements.txt
python -m pipeline.main --demo     # 가짜 데이터로 전체 실행 → docs/data/latest.json
python -m http.server -d docs      # http://localhost:8000
```

## 코인 탭
4시간마다(한국시간 01·05·09·13·17·21시 11분) `coin` 작업이 돈다. API 키·서버 필요 없음.

- **데이터**: 바이낸스 시세 전용 주소(`data-api.binance.vision`, 미국 서버에서도 열림) 일봉·4시간봉 + Coin Metrics 무료 커뮤니티 API(MVRV·해시레이트·채굴 발행액·거래소 순유입). 바이낸스 상장 전 구간은 Coin Metrics 종가.
- **온체인 국면**: BTC MVRV의 최근 4년(반감기 한 주기) 백분위 — 하위 20% 바닥권, 상위 20% 과열권. 2년 기준은 백테스트에서 실패해서 4년으로 바꿨다(과최적화 위험 있음).
- **프리셋**: 단순 보유 · 200일선 추세 · 역추세 기본(365일선 아래+RSI 30 미만 4분할 매수, 21일선이 365일선 +50%면 2분할 매도) · 역추세+온체인 · 온체인 단독 · UT Bot 일봉. 지표·전략 계산은 `docs/coin-engine.js` 하나를 브라우저와 알림이 같이 쓴다.
- **내 조합 전략**: 조건 전략(코인 비중·매수 조건 AND·매도 조건 OR·분할·2배 ETF 근사)과 전략 묶음. 폰 브라우저에 저장된다.
- **내 전략 알림**: 전략 화면의 '알림 받기'를 켜면 켠 전략 목록 전체를 ntfy 등록 주제(`<코인 주제>-reg`, 구독 불필요)로 보낸다. `coin-reg` 작업이 30분마다 새 등록을 확인해 `coin` 작업을 돌리고, `coin` 작업이 목록을 `coin.json`(state.mine)에 보관하며 매일 일봉 마감 후 새 매수·매도를 알린다. 서버 목록과 폰 목록이 다르면 앱이 열릴 때 다시 보낸다.
- **알림(ntfy)**: 코인 알림은 주식과 다른 주제(`config.json`의 `ntfy_topic_coin`)로 간다 — ntfy 앱에서 두 주제를 따로 구독. 대상은 `coin/config.json`에서 바꾼다. `alert_presets`×`alert_coins`의 매수·매도, `alert_ut4h` 코인의 UT Bot 4시간봉 전환, 온체인 국면 변화. 지난 상태는 배포된 `coin.json`에 남겨 같은 신호를 두 번 보내지 않는다.
- **UT Bot 설정**: `coin/config.json`의 `utbot`(일봉)·`utbot4h`(4시간봉) key·atr를 트레이딩뷰 설정과 맞춘다. 4시간봉은 기본 Key 5·ATR 20, 코인별로 `utbot4h.coins`에서 바꾼다(ETH 7·20 — 2018~2022로 고르고 2023~ 검증, SOL 5·20) (Key 1·ATR 10은 2018년부터 신호가 너무 잦아 손실). 일봉은 Key 2·ATR 20 (5·20이면 365일선 아래+MVRV 바닥권 조합 전략에서 매수가 0번이 됨).
- 로컬 실행: `NTFY_DRY=1 node coin/build.mjs` (Node 18 이상) → `docs/coinhist.json`, `docs/coin.json`
