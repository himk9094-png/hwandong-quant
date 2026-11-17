# 토스증권 Open API 국내 주식 자동매매 설계

2026-11-18 KST 기준. 공식 OpenAPI 스펙 1.2.28과 공개 가이드를 확인했다. 계좌 연결이나 실주문을 실행한 검증은 아직 없다. 목표는 환동퀀트의 전 거래일 종가 신호로 다음 거래일 국내 주식 시가 단일가에 참여하는 것이다.

## 인증과 배치 위치

토스증권 WTS의 **설정 → Open API**에서 `client_id`와 `client_secret`을 발급하고 **허용 IP 관리**에 주문 서버의 외부 송신 공인 IP를 등록한다. 고정 IP를 붙인 서버라면 같은 주소로 나가는지 배포 후 읽기 요청으로 확인한다. 키와 토큰은 주문 서버의 비밀 저장소에서만 읽고 로그에서 마스킹한다. GitHub Pages는 대시보드 표시만 담당한다.

호스트는 `https://openapi.tossinvest.com`이다. `POST /oauth2/token`에 `application/x-www-form-urlencoded`로 `grant_type=client_credentials`, `client_id`, `client_secret`을 보낸다. 응답의 `expires_in`으로 유효 시간을 계산하고 같은 프로세스군에서 토큰 재발급을 직렬화한다. REST는 `Authorization: Bearer <access_token>`을 사용한다. 계좌 목록에서 선택한 `accountSeq`는 보유, 주문, 주문 가능 금액 등 계좌별 요청의 `X-Tossinvest-Account` 헤더에 넣는다. 미등록 IP는 403이므로 설정을 확인하고 자동 호출을 멈춘다. [공식 시작 가이드](https://openapi.tossinvest.com/openapi-docs/overview.md)

## 사용 엔드포인트와 호출 한도

공식 한도는 클라이언트와 그룹 기준 초당 요청 수다. 성공·429 응답의 `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`을 읽고, 429에서는 `Retry-After`에 맞춰 재시도한다. 여러 프로세스가 호출하면 그룹별 큐와 한도를 공유한다.

| 그룹 | 요청 | 용도 | 초당 최대 |
| --- | --- | --- | ---: |
| AUTH | `POST /oauth2/token` | 토큰 발급 | 5 |
| ACCOUNT | `GET /api/v1/accounts` | 사용 계좌와 `accountSeq` 확인 | 1 |
| ASSET | `GET /api/v1/holdings` | 보유 주식, 평가금액, 손익 | 5 |
| TRANSACTION | `GET /api/v1/transactions` | 계좌 거래내역 확인 | 5 |
| ORDER_INFO | `GET /api/v1/buying-power?currency=KRW`, `GET /api/v1/sellable-quantity`, `GET /api/v1/commissions` | 주문 가용 현금, 매도 가능 수량, 적용 수수료 | 6 (09:00~09:10 KST에는 3) |
| ORDER | `POST /api/v1/orders`, `POST /api/v1/orders/{orderId}/modify`, `POST /api/v1/orders/{orderId}/cancel` | 주문 제출, 정정, 취소 | 10 |
| ORDER_HISTORY | `GET /api/v1/orders`, `GET /api/v1/orders/{orderId}` | 미결·종료 주문, 누적 체결 수량, 평균 체결가 | 5 |
| MARKET_INFO | `GET /api/v1/market-calendar/KR?date=YYYY-MM-DD` | 휴장과 통합 운영 시간 확인 | 3 |

시장 체결 시세는 `GET /api/v1/trades`로 조회한다. 내 주문의 부분체결과 종료 상태는 주문 상세 및 `wss://openapi-ws.tossinvest.com/ws/v1`의 `personal:order`를 사용한다. REST 주문 조회는 주문별 누적 결과를 반환한다. 웹소켓이 끊긴 동안의 이벤트는 재전송되지 않으므로 재연결 시 구독을 복구하고 REST로 미결 주문과 최근 종료 주문을 대조한다. [공식 REST 스펙](https://openapi.tossinvest.com/openapi-docs/latest/openapi.json) · [공식 한도 표](https://openapi.tossinvest.com/openapi-docs/overview.md#rate-limits) · [주문 이벤트](https://developers.tossinvest.com/docs/order-event)

## 장 시작 전 시가 단일가 주문

KRX 일반 영업일 시가 단일가 호가 접수는 08:30~09:00 KST다. 실행일의 국내 장 운영 정보를 확인한다. 응답의 `today.integrated`가 `null`이면 그날 주문을 건너뛴다. 통합 시각은 KRX와 NXT 세션을 묶은 정보이므로 `preMarket.startTime`을 KRX 시가 주문 접수 시작으로 사용하면 안 된다. 개장 지연일은 별도 거래소 공지와 대조하고, 일정이 맞지 않으면 주문을 보류한다. [금융위원회 시가 단일가 운영 설명](https://www.fsc.go.kr/po010106/83953) · [공식 FAQ](https://openapi.tossinvest.com/openapi-docs/faq.md)

`POST /api/v1/orders`의 국내 시가 단일가 조건은 `timeInForce: "OPG"`다. 시장가 `MARKET`과 지정가 `LIMIT`을 모두 지원한다. 아래는 전송 형태를 보여 주는 예시다.

```json
{
  "clientOrderId": "demo-kr-open-005930-buy",
  "symbol": "005930",
  "side": "BUY",
  "orderType": "MARKET",
  "timeInForce": "OPG",
  "quantity": "1"
}
```

국내 수량은 문자열로 보낸 양의 정수다. 시장가는 `price`를 생략하고 지정가는 유효 호가 단위의 `price`를 넣는다. 지정가는 가격 상·하한을 통제할 수 있지만 일부 또는 전부 미체결될 수 있다. Open API 일반 주문은 통합(SOR) 모드만 지원하며 요청에서 KRX를 직접 지정하는 필드는 없다. 토스 문서는 OPG를 국내 시가 단일가로 정의한다. 계좌·종목별 실제 접수 시각과 거래소 귀속은 소량 주문 검증 시 WTS 기록과 대조한다. 시가 단일가 미체결분을 정규장 시장가로 전환하려면 별도 정책이 필요하다. 기본 동작은 미체결 상태를 기록하고 후속 주문을 내지 않는 것이다. [공식 주문 스키마](https://openapi.tossinvest.com/openapi-docs/latest/openapi.json) · [공식 FAQ](https://openapi.tossinvest.com/openapi-docs/faq.md)

## 주문 실행과 장애 복구

1. 신호일과 실행일을 저장한다. 사용한 데이터의 종가 기준일이 예상 거래일과 다르거나 이전 실행이 끝나지 않았으면 자동 주문을 멈춘다.
2. 계좌 보유량·가용 현금·미결 주문을 읽어 목표 수량과의 차이만 주문안으로 만든다. 포트폴리오별 주문 금액 한도와 현금 여유분을 정한다. 시가 동시 주문에서는 당일 매도가 아직 체결되지 않은 자금을 매수 가능액에 미리 더하지 않는다.
3. 계좌·실행일·전략·종목·방향별 주문안을 DB에 고유 키로 저장한다. 제출 전에 최대 36자의 `clientOrderId`와 주문 본문을 기록하고 동일 주문안의 중복 실행을 잠근다.
4. 주문 응답을 놓치면 주문 내역을 먼저 조회한다. 최초 호출부터 10분 이내에는 같은 키와 같은 본문으로 재요청할 수 있다. `409 request-in-progress`면 대기 후 대조한다. 10분 뒤 같은 키의 재요청은 신규 주문이 되므로 결과가 불명확하면 수동 확인 상태로 둔다. 같은 종목의 반대방향 미체결 주문은 국내 주문 거절 사유이므로 계좌 기준으로 중복을 검사한다.
5. 접수와 체결을 구분해 기록한다. 부분체결 뒤 취소된 주문도 체결 수량을 보존한다. 주문 ID·누적 체결량·평균 체결가·비용을 저장하고 시가 구간 종료 후 잔고와 대조한다.
6. 읽기 전용 연결 → 발주 없이 주문안과 수량만 기록 → 승인된 소량 OPG 테스트 순서로 검증한다. OPG 접수·부분체결·미체결·타임아웃·재시작 복구·휴장을 확인한 뒤 반복 실행을 활성화한다.

## 비용 입력

토스증권 상품 안내에 적힌 국내 기본 매매수수료는 KRX 체결 0.015%, NXT 체결 0.014%다. 미국 기본은 0.1%이며 주문건별 총 체결금액이 10달러 이하이면 매매수수료가 면제된다. 세금 및 제비용은 별도다. `GET /api/v1/commissions` 응답은 시장별 단일 수수료율을 제공한다. 예를 들어 `0.00015`는 0.015%다. 주문 전 추정에는 계좌 적용 수수료를 쓰고, 체결 후에는 실제 수수료와 매도세를 대조한다. 시가 체결과 전일 종가의 간격, 시가 단일가 미체결, 비용은 각각 기록한다. [토스증권 상품 안내](https://home.tossinvest.com/ko/open-api) · [공식 FAQ](https://openapi.tossinvest.com/openapi-docs/faq.md)
