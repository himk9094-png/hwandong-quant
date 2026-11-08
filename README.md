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
- **주가**: KRX Open API 일별매매정보. 액면분할·무상증자 등은 KRX 기준가로 자동 보정(수정주가).
- **재무**: DART 다중회사 주요계정. 손익은 최근 4분기 합(TTM). 연결 우선, 없으면 별도. 전년 연간 자료가 없으면 연환산(`*` 표시).
- **마법공식은 근사식**이다. 주요계정 API에는 현금·차입금이 없어서 EV는 `시총 + 부채총계`, 투하자본은 `유동자산 − 유동부채 + 비유동자산`으로 계산한다.
- **A안 탭은 근사치**다. 기존 A안의 DART 제외 규칙은 아직 반영하지 않았다.
- **급등후보는 검증 전 점수**다. 성과 탭에 결과가 3개월 이상 쌓이기 전까지는 매매 근거로 쓰지 말 것.
- 데이터는 T-1(전 거래일) 종가 기준이다. 장중 실시간 신호가 아니다.

## 로컬 테스트
```bash
pip install -r requirements.txt
python -m pipeline.main --demo     # 가짜 데이터로 전체 실행 → docs/data/latest.json
python -m http.server -d docs      # http://localhost:8000
```
