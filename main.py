# ============================================================
#  리스크 관리 네비게이터 — main.py
#  KIS OpenAPI (실전계좌) + Streamlit
#  작성기준: KIS Developers 공식 문서 / GitHub open-trading-api
#  참고: https://apiportal.koreainvestment.com
#        https://github.com/koreainvestment/open-trading-api
# ============================================================

import streamlit as st
import pandas as pd
import requests
import json
import os
import time
import datetime
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# ──────────────────────────────────────────────
# 0. 페이지 기본 설정
# ──────────────────────────────────────────────
st.set_page_config(
    page_title="리스크 관리 네비게이터",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 전역 CSS — 다크 계열 퀀트 스타일
st.markdown(
    """
<style>
    /* 전체 배경 */
    .stApp { background-color: #0e1117; color: #e0e0e0; }
    /* 사이드바 */
    section[data-testid="stSidebar"] { background-color: #161b22; }
    /* 경고 배너 */
    .critical-banner {
        background: linear-gradient(90deg, #ff0000 0%, #8b0000 100%);
        color: white;
        font-size: 1.5rem;
        font-weight: 900;
        text-align: center;
        padding: 18px;
        border-radius: 10px;
        animation: blink 0.8s step-start infinite;
        letter-spacing: 2px;
    }
    @keyframes blink { 50% { opacity: 0.4; } }
    /* 카드 박스 */
    .card {
        background-color: #1c2333;
        border-radius: 10px;
        padding: 16px 20px;
        margin-bottom: 12px;
        border-left: 4px solid #4f8ef7;
    }
    /* 신호등 */
    .signal-ok   { color: #00e676; font-weight: 700; font-size:1.2rem; }
    .signal-warn { color: #ffeb3b; font-weight: 700; font-size:1.2rem; }
    .signal-crit { color: #f44336; font-weight: 700; font-size:1.2rem; }
    /* 섹션 제목 */
    .section-title {
        font-size: 1.1rem; font-weight: 700;
        color: #4f8ef7; border-bottom: 1px solid #2a3a5c;
        padding-bottom: 6px; margin-bottom: 12px;
    }
</style>
""",
    unsafe_allow_html=True,
)

# ──────────────────────────────────────────────
# 1. 상수 & 인증 설정
# ──────────────────────────────────────────────
KIS_REAL_DOMAIN = "https://openapi.koreainvestment.com:9443"
DATA_FILE = "data.json"  # 보유 종목 영속 저장 파일
TOKEN_CACHE_KEY = "_kis_token_cache"  # session_state 키
RATE_LIMIT_SEC = 0.22  # KIS REST 호출 최소 간격 (초) — 초당 최대 5회

# Secrets에서 인증 정보 로드
try:
    APP_KEY = st.secrets["app_key"]
    APP_SECRET = st.secrets["app_secret"]
    ACC_NO = st.secrets["acc_no"]  # "12345678-01" 형식
    ACC_NO_BODY = ACC_NO.replace("-", "")[:10]  # API 바디용: 10자리
    CANO = ACC_NO_BODY[:8]  # 계좌번호 앞 8자리
    ACNT_PRDT = ACC_NO_BODY[8:10]  # 계좌번호 뒤 2자리
    SECRETS_OK = True
except Exception:
    SECRETS_OK = False

# ──────────────────────────────────────────────
# 2. session_state 초기화
# ──────────────────────────────────────────────
if "holdings" not in st.session_state:
    st.session_state.holdings = []  # 보유 종목 리스트

if "cash_balance" not in st.session_state:
    st.session_state.cash_balance = 10_000_000  # 기본 현금 잔고

if "target_mdd" not in st.session_state:
    st.session_state.target_mdd = 20.0

if "last_api_call" not in st.session_state:
    st.session_state.last_api_call = 0.0

if TOKEN_CACHE_KEY not in st.session_state:
    st.session_state[TOKEN_CACHE_KEY] = {"token": None, "expires_at": 0}


# ──────────────────────────────────────────────
# 3. 데이터 영속성 — JSON 파일 읽기/쓰기
# ──────────────────────────────────────────────
def load_data_file():
    """앱 시작 시 data.json에서 보유 종목 로드"""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            st.session_state.holdings = data.get("holdings", [])
            st.session_state.cash_balance = data.get("cash_balance", 10_000_000)
            st.session_state.target_mdd = data.get("target_mdd", 20.0)
        except Exception as e:
            st.toast(f"⚠️ 데이터 파일 로드 오류: {e}", icon="⚠️")


def save_data_file():
    """보유 종목 변경 시 data.json에 저장"""
    try:
        data = {
            "holdings": st.session_state.holdings,
            "cash_balance": st.session_state.cash_balance,
            "target_mdd": st.session_state.target_mdd,
        }
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        st.toast(f"⚠️ 데이터 저장 오류: {e}", icon="⚠️")


# 앱 최초 실행 시 파일에서 로드
if "data_loaded" not in st.session_state:
    load_data_file()
    st.session_state.data_loaded = True


# ──────────────────────────────────────────────
# 4. KIS API — 토큰 발급 및 자동 갱신
#    참고: POST /oauth2/tokenP
#    https://apiportal.koreainvestment.com
# ──────────────────────────────────────────────
def _rate_limit():
    """KIS API 호출 간격 보장 (연속 호출 방지)"""
    elapsed = time.time() - st.session_state.last_api_call
    if elapsed < RATE_LIMIT_SEC:
        time.sleep(RATE_LIMIT_SEC - elapsed)
    st.session_state.last_api_call = time.time()


def get_access_token() -> str | None:
    """
    액세스 토큰 반환 (캐시된 토큰이 유효하면 재사용,
    만료 5분 전부터 자동 갱신).
    오류 시 None 반환.
    """
    if not SECRETS_OK:
        return None

    cache = st.session_state[TOKEN_CACHE_KEY]
    # 만료 5분(300초) 전에 갱신
    if cache["token"] and time.time() < cache["expires_at"] - 300:
        return cache["token"]

    url = f"{KIS_REAL_DOMAIN}/oauth2/tokenP"
    body = {
        "grant_type": "client_credentials",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
    }
    try:
        _rate_limit()
        resp = requests.post(url, json=body, timeout=10)
        resp.raise_for_status()
        result = resp.json()
        token = result.get("access_token")
        # 만료시간: 응답의 expires_in(초) 기준, 없으면 기본 86400초(24시간)
        expires_in = int(result.get("expires_in", 86400))
        st.session_state[TOKEN_CACHE_KEY] = {
            "token": token,
            "expires_at": time.time() + expires_in,
        }
        return token
    except requests.exceptions.ConnectionError:
        st.error("🔌 KIS 서버 연결 실패 — 네트워크를 확인하세요.")
    except requests.exceptions.Timeout:
        st.error("⏰ KIS API 응답 시간 초과 (Timeout).")
    except requests.exceptions.HTTPError as e:
        code = e.response.status_code if e.response else "N/A"
        st.error(f"🔑 토큰 발급 실패 (HTTP {code}) — App Key/Secret을 확인하세요.")
    except Exception as e:
        st.error(f"❌ 예기치 않은 오류: {e}")
    return None


def _kis_get(tr_id: str, path: str, params: dict) -> dict | None:
    """
    KIS REST GET 공통 호출   �수.
    토큰 만료·Rate Limit·네트워크 오류를 모두 처리.
    """
    token = get_access_token()
    if not token:
        return None

    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": tr_id,
        "custtype": "P",  # 개인
    }
    url = f"{KIS_REAL_DOMAIN}{path}"
    try:
        _rate_limit()
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        if resp.status_code == 429:
            st.toast("⚡ API 호출 한도 초과 — 잠시 후 재시도합니다.", icon="⚡")
            time.sleep(1.0)
            return None
        if resp.status_code == 401:
            # 토큰 강제 만료 처리 후 재시도
            st.session_state[TOKEN_CACHE_KEY] = {"token": None, "expires_at": 0}
            st.toast("🔄 토큰 만료 — 자동 재발급 중...", icon="🔄")
            return None
        resp.raise_for_status()
        data = resp.json()
        if data.get("rt_cd") != "0":
            msg = data.get("msg1", "알 수 없는 오류")
            st.toast(f"📡 API 오류: {msg}", icon="📡")
            return None
        return data
    except requests.exceptions.ConnectionError:
        st.toast("🔌 네트워크 연결 오류", icon="🔌")
    except requests.exceptions.Timeout:
        st.toast("⏰ API 응답 시간 초과", icon="⏰")
    except Exception as e:
        st.toast(f"❌ API 호출 오류: {e}", icon="❌")
    return None


# ──────────────────────────────────────────────
# 5. KIS API — 개별 데이터 조회 함수
# ──────────────────────────────────────────────


@st.cache_data(ttl=60)  # 1분 캐싱 (Rate Limit 대응)
def fetch_stock_price(ticker: str) -> dict | None:
    """
    주식 현재가 시세 조회
    TR_ID: FHKST01010100
    Path : /uapi/domestic-stock/v1/quotations/inquire-price
    출처 : https://apiportal.koreainvestment.com (국내주식-008)
    """
    data = _kis_get(
        tr_id="FHKST01010100",
        path="/uapi/domestic-stock/v1/quotations/inquire-price",
        params={
            "FID_COND_MRKT_DIV_CODE": "J",  # J: 주식
            "FID_INPUT_ISCD": ticker,
        },
    )
    if data and "output" in data:
        o = data["output"]
        return {
            "name": o.get("hts_kor_isnm", ticker),
            "price": int(o.get("stck_prpr", 0)),
            "change_rate": float(o.get("prdy_ctrt", 0)),
            "per": float(o.get("per", 0)),
            "pbr": float(o.get("pbr", 0)),
            "volume": int(o.get("acml_vol", 0)),
            "high_52w": int(o.get("w52_hgpr", 0)),
            "low_52w": int(o.get("w52_lwpr", 0)),
            "ma20": float(o.get("d20_dsrt", 0)),  # 20일 이격도 (없으면 별도 조회)
        }
    return None


@st.cache_data(ttl=60)
def fetch_kospi_index() -> dict | None:
    """
    코스피 지수 현재가 조회
    TR_ID: FHPUP02100000
    Path : /uapi/domestic-stock/v1/quotations/inquire-index-price
    출처 : KIS Developers 국내주식 지수 시세
    """
    data = _kis_get(
        tr_id="FHPUP02100000",
        path="/uapi/domestic-stock/v1/quotations/inquire-index-price",
        params={
            "FID_COND_MRKT_DIV_CODE": "U",  # U: 지수
            "FID_INPUT_ISCD": "0001",  # 0001: 코스피
        },
    )
    if data and "output" in data:
        o = data["output"]
        return {
            "index": float(o.get("bstp_nmix_prpr", 0)),
            "change_rate": float(o.get("bstp_nmix_prdy_ctrt", 0)),
            "change_val": float(o.get("bstp_nmix_prdy_vrss", 0)),
        }
    return None


@st.cache_data(ttl=60)
def fetch_investor_trend(ticker: str) -> dict | None:
    """
    종목별 투자자 매매동향 (외인·기관·개인 순매수)
    TR_ID: FHKST01010900
    Path : /uapi/domestic-stock/v1/quotations/inquire-investor
    출처 : KIS Developers 국내주식-009
    """
    data = _kis_get(
        tr_id="FHKST01010900",
        path="/uapi/domestic-stock/v1/quotations/inquire-investor",
        params={
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": ticker,
        },
    )
    if data and "output" in data:
        records = []
        for row in data["output"]:
            records.append(
                {
                    "date": row.get("stck_bsop_date", ""),
                    "foreign": int(row.get("frgn_ntby_qty", 0)),  # 외인 순매수
                    "organ": int(row.get("orgn_ntby_qty", 0)),  # 기관 순매수
                    "retail": int(row.get("prsn_ntby_qty", 0)),  # 개인 순매수
                }
            )
        return {"records": records}
    return None


@st.cache_data(ttl=300)  # 5분 캐싱 (장중 투자자 동향은 덜 빈번해도 무방)
def fetch_market_investor() -> dict | None:
    """
    시장 전체 투자자별 매매동향 (코스피)
    TR_ID: FHKST01010900 → 시장 전체는 별도 TR 사용
    TR_ID: FHKST03020200
    Path : /uapi/domestic-stock/v1/quotations/inquire-member
    ※ 시장 전체 외인/기관/개인 데이터 — 공식 문서 참조
    출처 : KIS Developers 국내주식-037
    """
    data = _kis_get(
        tr_id="FHKST03020200",
        path="/uapi/domestic-stock/v1/quotations/inquire-member",
        params={
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": "0001",
        },
    )
    # 실제 응답 구조가 증권사 정책에 따라 다를 수 있어 안전하게 처리
    if data and "output1" in data:
        o = data["output1"]
        if isinstance(o, list) and len(o) > 0:
            o = o[0]
        return {
            "foreign": int(o.get("frgn_ntby_tr_pbmn", 0)),
            "organ": int(o.get("orgn_ntby_tr_pbmn", 0)),
            "retail": int(o.get("prsn_ntby_tr_pbmn", 0)),
        }
    return None


# ──────────────────────────────────────────────
# 6. 리스크 계산 로직
# ──────────────────────────────────────────────


def calc_relative_signal(stock_ret: float, kospi_ret: float) -> float:
    """
    상대적 손절 시그널
    Signal = 종목 수익률(%) - 매수 이후 코스피 변동률(%)
    출처: 기획서 리스크 관리 로직
    """
    return stock_ret - kospi_ret


def calc_position_size(
    total_assets: float, current_price: float, stop_price: float, risk_pct: float = 0.02
) -> int:
    """
    포지션 사이징 — 2% 룰
    최적 주수 = (총 자산 × risk_pct) / (현재가 - 손절가)
    손절가가 현재가 이상이면 0 반환 (오입력 방지)
    """
    risk_amount = total_assets * risk_pct
    risk_per_share = current_price - stop_price
    if risk_per_share <= 0:
        return 0
    return max(0, int(risk_amount / risk_per_share))


def calc_stock_return(avg_price: float, current_price: float) -> float:
    """종목 수익률(%)"""
    if avg_price <= 0:
        return 0.0
    return (current_price - avg_price) / avg_price * 100


def calc_kospi_return(buy_kospi: float, current_kospi: float) -> float:
    """매수 시점 대비 코스피 변동률(%)"""
    if buy_kospi <= 0:
        return 0.0
    return (current_kospi - buy_kospi) / buy_kospi * 100


def get_signal_status(signal: float) -> tuple[str, str]:
    """
    신호등 상태 반환
    Returns: (상태 라벨, CSS 클래스)
    """
    if signal <= -10:
        return "🔴 CRITICAL — 즉시 손절", "signal-crit"
    elif signal <= -5:
        return "🟡 WARNING — 점검 필요", "signal-warn"
    else:
        return "🟢 NORMAL", "signal-ok"


def can_add_buy(stock_ret: float, above_ma20: bool) -> bool:
    """
    불타기 가능 여부:
    수익률 +5% 이상 AND 20일선 상향 돌파 시만 허용
    """
    return stock_ret >= 5.0 and above_ma20


# ──────────────────────────────────────────────
# 7. 사이드바 UI
# ──────────────────────────────────────────────

with st.sidebar:
    st.markdown("## 🛡️ 리스크 네비게이터")
    st.caption("KIS OpenAPI 연동 리스크 관리 도구")

    # ── 계좌 기본 정보 ──
    st.markdown("---")
    st.markdown("### 💰 계좌 설정")
    cash = st.number_input(
        "현금 잔고 (원)",
        min_value=0,
        value=st.session_state.cash_balance,
        step=100_000,
        format="%d",
    )
    if cash != st.session_state.cash_balance:
        st.session_state.cash_balance = cash
        save_data_file()

    mdd = st.slider(
        "목표 최대 낙폭 (MDD) %",
        min_value=5,
        max_value=50,
        value=int(st.session_state.target_mdd),
        step=1,
    )
    if mdd != st.session_state.target_mdd:
        st.session_state.target_mdd = float(mdd)
        save_data_file()

    # ── 보유 종목 추가 ──
    st.markdown("---")
    st.markdown("### 📋 보유 종목 추가")

    with st.form("add_holding_form", clear_on_submit=True):
        ticker = st.text_input("종목 코드 (6자리)", placeholder="005930")
        name = st.text_input("종목명 (직접 입력)", placeholder="삼성전자")
        avg_p = st.number_input("평단가 (원)", min_value=1, value=70000, step=100)
        qty = st.number_input("수량 (주)", min_value=1, value=10, step=1)
        buy_kos = st.number_input(
            "매수 시점 코스피", min_value=100.0, value=2500.0, step=1.0, format="%.2f"
        )
        submitted = st.form_submit_button("➕ 종목 추가")

        if submitted:
            if not ticker or len(ticker) != 6 or not ticker.isdigit():
                st.warning("종목 코드는 6자리 숫자여야 합니다.")
            else:
                # 중복 체크
                exists = any(h["ticker"] == ticker for h in st.session_state.holdings)
                if exists:
                    st.warning(f"{ticker} 이미 추가된 종목입니다.")
                else:
                    st.session_state.holdings.append(
                        {
                            "ticker": ticker,
                            "name": name if name else ticker,
                            "avg_price": avg_p,
                            "qty": qty,
                            "buy_kospi": buy_kos,
                        }
                    )
                    save_data_file()
                    st.success(f"✅ {name or ticker} 추가 완료")

    # ── 보유 종목 목록 & 삭제 ──
    if st.session_state.holdings:
        st.markdown("---")
        st.markdown("### 📌 등록 종목")
        for i, h in enumerate(st.session_state.holdings):
            col1, col2 = st.columns([3, 1])
            col1.caption(f"{h['name']} ({h['ticker']})")
            if col2.button("🗑️", key=f"del_{i}"):
                st.session_state.holdings.pop(i)
                save_data_file()
                st.rerun()

    st.markdown("---")
    st.caption("🔄 1분 간격 자동 갱신")
    auto_refresh = st.checkbox("자동 새로고침 (1분)", value=False)

# ──────────────────────────────────────────────
# 8. 메인 대시보드
# ──────────────────────────────────────────────

st.markdown("# 🛡️ 리스크 관리 네비게이터")
st.markdown(
    f"<span style='color:#888;font-size:0.85rem;'>"
    f"기준 시각: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</span>",
    unsafe_allow_html=True,
)

if not SECRETS_OK:
    st.error(
        "⚠️ Streamlit Secrets에 `app_key`, `app_secret`, `acc_no`가 설정되지 않았습니다.\n\n"
        "Replit → Secrets 탭에서 해당 값을 등록해 주세요."
    )
    st.stop()

# ── 코스피 데이터 조회 ──
kospi_data = fetch_kospi_index()
current_kospi = kospi_data["index"] if kospi_data else None

# ──────────────────────────────────────────────
# 섹션 A: 시장 상황 (Top Section)
# ──────────────────────────────────────────────
st.markdown("---")
st.markdown('<p class="section-title">📊 시장 현황</p>', unsafe_allow_html=True)

col_k1, col_k2, col_k3, col_k4 = st.columns(4)

if kospi_data:
    k_chg = kospi_data["change_rate"]
    k_delta_color = "normal" if k_chg >= 0 else "inverse"
    col_k1.metric(
        label="🇰🇷 KOSPI",
        value=f"{kospi_data['index']:,.2f}",
        delta=f"{k_chg:+.2f}%",
        delta_color=k_delta_color,
    )
else:
    col_k1.metric("🇰🇷 KOSPI", "조회 실패", "—")

# 시장 투자자 동향
market_inv = fetch_market_investor()
if market_inv:
    col_k2.metric(
        "🌍 외인 순매수",
        f"{market_inv['foreign']:+,}백만",
        delta=None,
    )
    col_k3.metric(
        "🏦 기관 순매수",
        f"{market_inv['organ']:+,}백만",
        delta=None,
    )
    col_k4.metric(
        "👤 개인 순매수",
        f"{market_inv['retail']:+,}백만",
        delta=None,
    )
else:
    col_k2.metric("🌍 외인", "조회 실패", "—")
    col_k3.metric("🏦 기관", "조회 실패", "—")
    col_k4.metric("👤 개인", "조회 실패", "—")

# ──────────────────────────────────────────────
# 섹션 B: 포트폴리오 (Middle Section)
# ──────────────────────────────────────────────
st.markdown("---")
st.markdown('<p class="section-title">📂 내 포트폴리오</p>', unsafe_allow_html=True)

if not st.session_state.holdings:
    st.info("좌측 사이드바에서 보유 종목을 추가해 주세요.")
else:
    # 전체 상대 손절 CRITICAL 여부 감지
    any_critical = False
    portfolio_rows = []

    for h in st.session_state.holdings:
        ticker = h["ticker"]
        avg_price = h["avg_price"]
        qty = h["qty"]
        buy_kospi = h["buy_kospi"]

        # 현재가 조회
        price_data = fetch_stock_price(ticker)
        if price_data:
            cur_price = price_data["price"]
            per = price_data["per"]
            pbr = price_data["pbr"]
            volume = price_data["volume"]
            name = price_data["name"]
            chg_rate = price_data["change_rate"]
            ma20 = price_data["ma20"]  # 20일 이격도 (>100 → 상향 돌파)
        else:
            cur_price = avg_price  # 폴백
            per = pbr = volume = chg_rate = ma20 = 0
            name = h["name"]

        # 수익률 계산
        stock_ret = calc_stock_return(avg_price, cur_price)
        kospi_ret = (
            calc_kospi_return(buy_kospi, current_kospi) if current_kospi else 0.0
        )
        signal_val = calc_relative_signal(stock_ret, kospi_ret)
        status_lbl, status_cls = get_signal_status(signal_val)

        # 평가금액
        eval_amount = cur_price * qty
        profit_loss = (cur_price - avg_price) * qty

        if signal_val <= -10:
            any_critical = True

        # 불타기 가능 여부 (20일 이격도 > 100이면 20일선 상향)
        above_ma20 = ma20 > 100
        can_add = can_add_buy(stock_ret, above_ma20)

        portfolio_rows.append(
            {
                "종목명": name,
                "코드": ticker,
                "현재가(원)": cur_price,
                "평단가(원)": avg_price,
                "수량": qty,
                "평가금액(원)": eval_amount,
                "손익(원)": profit_loss,
                "종목수익률(%)": round(stock_ret, 2),
                "코스피변동(%)": round(kospi_ret, 2),
                "상대Gap(%)": round(signal_val, 2),
                "신호": status_lbl,
                "PER": per,
                "PBR": pbr,
                "거래량": volume,
                "불타기가능": "✅" if can_add else "❌",
            }
        )

    # ── 전체 CRITICAL 경고 배너 ──
    if any_critical:
        st.markdown(
            '<div class="critical-banner">'
            "🚨 CRITICAL ALERT — 상대 수익률 Gap -10% 돌파 종목 발생! 즉시 매도를 검토하세요! 🚨"
            "</div>",
            unsafe_allow_html=True,
        )

    # ── 포트폴리오 데이터프레임 ──
    df_port = pd.DataFrame(portfolio_rows)

    def style_signal(val: str) -> str:
        if "CRITICAL" in val:
            return "background-color:#8b0000;color:white;font-weight:700"
        elif "WARNING" in val:
            return "background-color:#5c4a00;color:#ffeb3b;font-weight:700"
        return "color:#00e676"

    def style_gap(val):
        if val <= -10:
            return "background-color:#8b0000;color:white;font-weight:700"
        elif val <= -5:
            return "color:#ffeb3b"
        elif val >= 0:
            return "color:#00e676"
        return ""

    styled_df = (
        df_port.style.applymap(style_signal, subset=["신호"])
        .applymap(style_gap, subset=["상대Gap(%)"])
        .format(
            {
                "현재가(원)": "{:,}",
                "평단가(원)": "{:,}",
                "평가금액(원)": "{:,}",
                "손익(원)": "{:+,}",
                "종목수익률(%)": "{:+.2f}%",
                "코스피변동(%)": "{:+.2f}%",
                "상대Gap(%)": "{:+.2f}%",
                "PER": "{:.1f}",
                "PBR": "{:.2f}",
                "거래량": "{:,}",
            }
        )
    )
    st.dataframe(styled_df, use_container_width=True, height=300)

    # ── 요약 지표 ──
    total_eval = sum(r["평가금액(원)"] for r in portfolio_rows)
    total_pl = sum(r["손익(원)"] for r in portfolio_rows)
    total_assets = st.session_state.cash_balance + total_eval
    total_pl_pct = (
        total_pl / (total_assets - total_pl) * 100 if total_assets != total_pl else 0
    )

    mc1, mc2, mc3, mc4 = st.columns(4)
    mc1.metric("💼 총 평가금액", f"{total_eval:,}원")
    mc2.metric("💵 현금 잔고", f"{st.session_state.cash_balance:,}원")
    mc3.metric("📈 총 손익", f"{total_pl:+,}원", delta=f"{total_pl_pct:+.2f}%")
    mc4.metric("🏦 총 자산", f"{total_assets:,}원")

    # ── 수익률 비교 차트 (Plotly) ──
    st.markdown("---")
    st.markdown(
        '<p class="section-title">📈 종목별 수익률 vs 코스피 비교</p>',
        unsafe_allow_html=True,
    )

    fig = go.Figure()

    tickers_label = [r["종목명"] for r in portfolio_rows]
    stock_rets = [r["종목수익률(%)"] for r in portfolio_rows]
    kospi_rets = [r["코스피변동(%)"] for r in portfolio_rows]
    gap_vals = [r["상대Gap(%)"] for r in portfolio_rows]

    fig.add_trace(
        go.Bar(
            name="종목 수익률(%)",
            x=tickers_label,
            y=stock_rets,
            marker_color=["#f44336" if v < 0 else "#00e676" for v in stock_rets],
            opacity=0.85,
        )
    )
    fig.add_trace(
        go.Scatter(
            name="코스피 변동(%)",
            x=tickers_label,
            y=kospi_rets,
            mode="lines+markers",
            line=dict(color="#4f8ef7", width=2, dash="dash"),
            marker=dict(size=8),
        )
    )
    fig.add_trace(
        go.Scatter(
            name="상대 Gap(%)",
            x=tickers_label,
            y=gap_vals,
            mode="lines+markers",
            line=dict(color="#ffeb3b", width=2),
            marker=dict(size=8, symbol="diamond"),
        )
    )
    # -10% 손절선
    fig.add_hline(
        y=-10,
        line_dash="dot",
        line_color="red",
        annotation_text="손절선 -10%",
        annotation_position="top right",
    )
    fig.add_hline(y=0, line_color="#555", line_width=1)

    fig.update_layout(
        plot_bgcolor="#0e1117",
        paper_bgcolor="#0e1117",
        font=dict(color="#e0e0e0"),
        legend=dict(bgcolor="#1c2333", bordercolor="#2a3a5c"),
        xaxis=dict(gridcolor="#2a3a5c"),
        yaxis=dict(gridcolor="#2a3a5c", ticksuffix="%"),
        height=380,
        margin=dict(l=30, r=30, t=30, b=30),
        bargap=0.3,
    )
    st.plotly_chart(fig, use_container_width=True)

    # ── 종목별 외인/기관 수급 차트 ──
    st.markdown("---")
    st.markdown(
        '<p class="section-title">🌊 종목별 외인·기관 수급 추세</p>',
        unsafe_allow_html=True,
    )

    inv_cols = st.columns(min(len(st.session_state.holdings), 3))

    for idx, h in enumerate(st.session_state.holdings):
        col_idx = idx % 3
        ticker = h["ticker"]
        inv_data = fetch_investor_trend(ticker)

        with inv_cols[col_idx]:
            st.caption(f"**{h['name']} ({ticker})**")
            if inv_data and inv_data["records"]:
                df_inv = pd.DataFrame(inv_data["records"])
                df_inv = df_inv.sort_values("date").tail(10)  # 최근 10거래일

                fig_inv = go.Figure()
                fig_inv.add_trace(
                    go.Bar(
                        name="외인",
                        x=df_inv["date"],
                        y=df_inv["foreign"],
                        marker_color="#4f8ef7",
                        opacity=0.8,
                    )
                )
                fig_inv.add_trace(
                    go.Bar(
                        name="기관",
                        x=df_inv["date"],
                        y=df_inv["organ"],
                        marker_color="#ab47bc",
                        opacity=0.8,
                    )
                )
                fig_inv.update_layout(
                    plot_bgcolor="#0e1117",
                    paper_bgcolor="#1c2333",
                    font=dict(color="#e0e0e0", size=10),
                    height=200,
                    margin=dict(l=10, r=10, t=10, b=10),
                    legend=dict(font=dict(size=9)),
                    barmode="group",
                    xaxis=dict(gridcolor="#2a3a5c", tickangle=45),
                    yaxis=dict(gridcolor="#2a3a5c"),
                )
                st.plotly_chart(fig_inv, use_container_width=True)
            else:
                st.caption("수급 데이터 조회 실패")

# ──────────────────────────────────────────────
# 섹션 C: 의사결정 도구 (Bottom Section)
# ──────────────────────────────────────────────
st.markdown("---")
st.markdown('<p class="section-title">🧮 의사결정 도구</p>', unsafe_allow_html=True)

tab1, tab2 = st.tabs(["📐 매수 계산기 (2% 룰)", "🔔 매도 신호등"])

# ── Tab 1: 매수 계산기 ──
with tab1:
    st.markdown("#### 신규 포지션 최적 주수 계산")
    st.caption(
        "총 자산의 2%를 최대 손실로 가정하여 최적 매수 주수를 산출합니다.\n\n"
        "공식: **최적 주수 = (총 자산 × 2%) ÷ (현재가 − 손절가)**"
    )

    bc1, bc2 = st.columns(2)
    with bc1:
        calc_ticker = st.text_input(
            "종목 코드 (조회)", placeholder="005930", key="calc_ticker"
        )
        calc_cur_auto = st.checkbox("현재가 자동 조회 (KIS API)", value=True)

        if calc_ticker and calc_cur_auto and len(calc_ticker) == 6:
            with st.spinner("현재가 조회 중..."):
                auto_price_data = fetch_stock_price(calc_ticker)
            if auto_price_data:
                st.success(
                    f"현재가: **{auto_price_data['price']:,}원** "
                    f"({auto_price_data['change_rate']:+.2f}%)"
                )
                default_price = auto_price_data["price"]
            else:
                st.warning("조회 실패 — 수동으로 입력하세요.")
                default_price = 50000
        else:
            default_price = 50000

        calc_price = st.number_input(
            "현재가 (원)",
            min_value=1,
            value=default_price,
            step=100,
            key="calc_price",
        )
        calc_stop = st.number_input(
            "손절가 (원)",
            min_value=1,
            value=max(1, int(default_price * 0.93)),  # 기본 -7%
            step=100,
            key="calc_stop",
        )

    with bc2:
        # 총 자산 = 현금 + 평가금액
        total_eval_sum = (
            sum(
                fetch_stock_price(h["ticker"])["price"] * h["qty"]
                if fetch_stock_price(h["ticker"])
                else h["avg_price"] * h["qty"]
                for h in st.session_state.holdings
            )
            if st.session_state.holdings
            else 0
        )

        total_asset_calc = st.session_state.cash_balance + total_eval_sum
        st.metric("현재 총 자산", f"{total_asset_calc:,}원")
        risk_pct_input = st.slider("리스크 비율(%)", 1, 5, 2, 1, key="risk_pct")

        if calc_price > calc_stop:
            optimal_qty = calc_position_size(
                total_asset_calc, calc_price, calc_stop, risk_pct_input / 100
            )
            max_loss = (calc_price - calc_stop) * optimal_qty
            stop_loss_pct = (calc_price - calc_stop) / calc_price * 100

            st.markdown(
                f"""
            <div class="card">
            <b>최적 매수 주수:</b>
            <span style="font-size:2rem;color:#4f8ef7;font-weight:900;">
            {optimal_qty:,} 주</span><br>
            총 매수금액: {calc_price * optimal_qty:,}원<br>
            최대 손실액: {max_loss:,}원
            ({risk_pct_input}% 룰 기준)<br>
            손절 하락률: {stop_loss_pct:.1f}%<br>
            손익비 기준 목표가:
            <b>{int(calc_price + (calc_price - calc_stop) * 2):,}원</b>
            (1:2 손익비)
            </div>
            """,
                unsafe_allow_html=True,
            )
        else:
            st.warning("⚠️ 손절가는 현재가보다 낮아야 합니다.")

# ── Tab 2: 매도 신호등 ──
with tab2:
    st.markdown("#### 보유 종목 매도 신호등")
    st.caption(
        "상대 수익률 Gap = 종목 수익률 − 코스피 변동률\n\n"
        "- 🟢 NORMAL: Gap > -5%\n"
        "- 🟡 WARNING: -10% < Gap ≤ -5%\n"
        "- 🔴 CRITICAL: Gap ≤ -10% → **즉시 손절 권고**"
    )

    if not st.session_state.holdings:
        st.info("보유 종목을 추가하면 신호등이 활성화됩니다.")
    else:
        for h in st.session_state.holdings:
            ticker = h["ticker"]
            avg_price = h["avg_price"]

            price_data = fetch_stock_price(ticker)
            cur_price = price_data["price"] if price_data else avg_price
            kospi_ret = (
                calc_kospi_return(h["buy_kospi"], current_kospi)
                if current_kospi
                else 0.0
            )
            stock_ret = calc_stock_return(avg_price, cur_price)
            signal_val = calc_relative_signal(stock_ret, kospi_ret)
            status_lbl, status_cls = get_signal_status(signal_val)

            # 불타기/물타기 버튼 활성화 여부
            price_info = fetch_stock_price(ticker)
            ma20 = price_info["ma20"] if price_info else 0
            above_ma20 = ma20 > 100
            can_add = can_add_buy(stock_ret, above_ma20)

            with st.container():
                sc1, sc2, sc3 = st.columns([2, 2, 2])
                sc1.markdown(
                    f"**{h['name']}** ({ticker})<br>"
                    f"<small>종목: {stock_ret:+.2f}% | 코스피: {kospi_ret:+.2f}%</small>",
                    unsafe_allow_html=True,
                )
                sc2.markdown(
                    f'<span class="{status_cls}">{status_lbl}</span><br>'
                    f"<small>Gap: {signal_val:+.2f}%</small>",
                    unsafe_allow_html=True,
                )

                # 불타기/물타기 버튼
                if can_add:
                    sc3.success("✅ 불타기 가능 (수익+5% & 20일선↑)")
                else:
                    sc3.button(
                        f"🚫 추가 매수 불가 — {h['name']}",
                        disabled=True,
                        key=f"buy_disabled_{ticker}",
                        help="수익률이 +5% 미만이거나 20일선 아래입니다. (물타기 금지)",
                    )
                st.markdown("---")

# ──────────────────────────────────────────────
# 9. 자동 새로고침
# ──────────────────────────────────────────────
if auto_refresh:
    time.sleep(60)
    st.rerun()

# ──────────────────────────────────────────────
# 푸터
# ──────────────────────────────────────────────
st.markdown(
    "<div style='text-align:center;color:#444;font-size:0.75rem;margin-top:40px;'>"
    "리스크 관리 네비게이터 | KIS OpenAPI 연동 | "
    "데이터 출처: 한국투자증권 KIS Developers "
    "(https://apiportal.koreainvestment.com) | "
    "본 앱은 투자 참고용이며, 투자 결정에 대한 책임은 본인에게 있습니다."
    "</div>",
    unsafe_allow_html=True,
)
