# meeting_scheduler.py
# Flask Blueprint: 会議の取得・候補時間検索・イベント作成 API（定期リフレッシュ想定）
# 追加・改善点：
#  - /me/calendarView を params= で安全に組み立て（+00:00→Z へ正規化）
#  - findMeetingTimes を「厳格」設定（isOrganizerOptional=False, minimumAttendeePercentage=100）
#  - server 側で attendeeAvailability を二重チェック（全員 Free の候補だけ通す）
#  - ★在宅系タイトル（例：在宅勤務/在宅/自宅/家）を Busy から除外した“厳格フォールバック”を実装
#    （calendarView で subject を見て Busy 集合を構築 → 全員の空きの交差を返す）
#  - Teams 会議トグル & 会議室（resource）を create_event に反映
#  - Graph の 401/InvalidAuthenticationToken を検知して tokenExpired を返す（フロントの再試行用）

import requests
from flask import Blueprint, request, jsonify
from datetime import datetime, timedelta, timezone

meeting_scheduler_bp = Blueprint('meeting_scheduler', __name__)
GRAPH_API_ENDPOINT = "https://graph.microsoft.com/v1.0"


# ---- 認証ヘッダー ----
def get_auth_header():
    access_token = request.headers.get('x-ms-token-aad-access-token')
    if not access_token:
        return None
    return {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}


# ---- IANA → Windows TZ / WindowsTZ → おおまかなオフセット（DST 無視の簡易版）----
IANA_TO_WINDOWS_TZ = {
    "Asia/Tokyo": "Tokyo Standard Time",
    "UTC": "UTC",
    "Etc/UTC": "UTC",
    "Europe/London": "GMT Standard Time",
    "Europe/Paris": "Romance Standard Time",
    "Europe/Berlin": "W. Europe Standard Time",
    "America/Los_Angeles": "Pacific Standard Time",
    "America/Denver": "Mountain Standard Time",
    "America/Chicago": "Central Standard Time",
    "America/New_York": "Eastern Standard Time",
    "Asia/Shanghai": "China Standard Time",
    "Asia/Hong_Kong": "China Standard Time",
    "Asia/Singapore": "Singapore Standard Time",
    "Asia/Seoul": "Korea Standard Time",
    "Australia/Sydney": "AUS Eastern Standard Time",
}

WINDOWS_TZ_OFFSET_HOURS = {
    "UTC": 0,
    "GMT Standard Time": 0,
    "W. Europe Standard Time": 1,
    "Romance Standard Time": 1,
    "China Standard Time": 8,
    "Singapore Standard Time": 8,
    "Tokyo Standard Time": 9,
    "Korea Standard Time": 9,
    "AUS Eastern Standard Time": 10,
    "Pacific Standard Time": -8,
    "Mountain Standard Time": -7,
    "Central Standard Time": -6,
    "Eastern Standard Time": -5,
}

def to_windows_tz(tz: str, default: str = "Tokyo Standard Time") -> str:
    if not tz:
        return default
    return IANA_TO_WINDOWS_TZ.get(tz, tz)

def tz_offset_hours(win_tz: str) -> int:
    return WINDOWS_TZ_OFFSET_HOURS.get(win_tz, 0)

def to_utc_z(dt_aware: datetime) -> str:
    if dt_aware.tzinfo is None:
        dt_aware = dt_aware.replace(tzinfo=timezone.utc)
    iso = dt_aware.astimezone(timezone.utc).isoformat()
    return iso.replace("+00:00", "Z")

def _handle_graph_http_error(e: requests.exceptions.HTTPError):
    """
    Graph APIのエラーをハンドリングし、トークン期限切れ（401 InvalidAuthenticationToken）
    の場合はフロントエンドに再認証を促すフラグを返します。
    """
    status = e.response.status_code if e.response is not None else 500
    text = e.response.text if e.response is not None else str(e)
    
    # トークン期限切れの判定を強化
    # 401 Unauthorized かつ、エラーメッセージに特定のキーワードが含まれる場合
    is_token_expired = False
    if status == 401:
        # Graph APIが返す典型的なトークンエラー
        if "InvalidAuthenticationToken" in text or "CompactToken validation failed" in text:
            is_token_expired = True
    
    payload = {
        "success": False,
        "error": f"Graph APIエラー: {text}",
    }
    
    if is_token_expired:
        # フロントエンドの再試行ロジックのためのフラグ
        payload["tokenExpired"] = True
        # ログにも明示
        logger.warning(f"Graph API token expired. Status: {status}, Msg: {text}")

    # ステータスコードはそのまま返す（フロントエンドのfetchでも401を検知できるようにする）
    return jsonify(payload), status



# ---- 過去 90 日の会議取得（引用用）----
@meeting_scheduler_bp.route('/api/meetings/past_meetings', methods=['GET'])
def get_past_meetings():
    headers = get_auth_header()
    if not headers:
        return jsonify({"success": False, "error": "認証トークンが見つかりません。"}), 401

    now_utc = datetime.now(timezone.utc)
    ninety_days_ago_utc = now_utc - timedelta(days=90)

    params = {
        "$select": "id,subject,start,end,organizer,attendees",
        "startDateTime": to_utc_z(ninety_days_ago_utc),
        "endDateTime": to_utc_z(now_utc),
        "$orderby": "start/dateTime desc",
        "$top": 50,
    }

    try:
        resp = requests.get(f"{GRAPH_API_ENDPOINT}/me/calendarView", headers=headers, params=params)
        resp.raise_for_status()
        events = resp.json().get('value', [])
        return jsonify({"success": True, "meetings": events})
    except requests.exceptions.HTTPError as e:
        return _handle_graph_http_error(e)
    except Exception as e:
        return jsonify({"success": False, "error": f"サーバーエラー: {str(e)}"}), 500


# ---- 空き時間探索（Graph 推薦 → 失敗/補正が必要なら “在宅件名対応” の厳格フォールバック）----
@meeting_scheduler_bp.route('/api/meetings/find_times', methods=['POST'])
def find_meeting_times():
    headers = get_auth_header()
    if not headers:
        return jsonify({"success": False, "error": "認証トークンが見つかりません。"}), 401

    data = request.get_json(silent=True) or {}

    user_tz_raw = data.get('timeZone') or 'Tokyo Standard Time'
    user_timezone = to_windows_tz(user_tz_raw, default='Tokyo Standard Time')

    attendees_from_request = [str(a).strip() for a in data.get('attendees', []) if a]

    # 自分のメールを追加
    try:
        me_resp = requests.get(f"{GRAPH_API_ENDPOINT}/me?$select=mail,userPrincipalName", headers=headers)
        me_resp.raise_for_status()
        me_json = me_resp.json()
        my_email = me_json.get('mail') or me_json.get('userPrincipalName')
        if my_email and my_email not in attendees_from_request:
            attendees_from_request.append(my_email)
    except requests.exceptions.HTTPError as e:
        return _handle_graph_http_error(e)

    start_local = data.get('start')
    end_local = data.get('end')
    if not start_local or not end_local:
        return jsonify({"success": False, "error": "開始・終了日時が指定されていません。"}), 400

    duration_minutes = int(data.get('durationMinutes', 30))
    ignore_titles = [t.strip() for t in data.get('ignoreTitles', []) if t]
    room_email = (data.get('roomEmail') or '').strip()

    # 1) まずは Graph の findMeetingTimes（厳格設定）で試す
    request_body = {
        "attendees": [
            {"emailAddress": {"address": email}, "type": "required"}
            for email in attendees_from_request
        ],
        "timeConstraint": {
            "activityDomain": "work",
            "timeSlots": [
                {
                    "start": {"dateTime": start_local, "timeZone": user_timezone},
                    "end": {"dateTime": end_local, "timeZone": user_timezone},
                }
            ],
        },
        "meetingDuration": f"PT{duration_minutes}M",
        "maxCandidates": 10,
        "isOrganizerOptional": False,        # 主催者も必須
        "returnSuggestionReasons": True,
        "minimumAttendeePercentage": 100,    # 全員 Free のみ
    }

    # 会議室の可用性も考慮（候補に部屋を含む）
    if room_email:
        request_body["locationConstraint"] = {
            "isRequired": True,
            "suggestLocation": False,
            "locations": [{
                "resolveAvailability": True,
                "displayName": room_email,
                "locationEmailAddress": room_email
            }]
        }

    try:
        resp = requests.post(f"{GRAPH_API_ENDPOINT}/me/findMeetingTimes", headers=headers, json=request_body)
        resp.raise_for_status()
        body = resp.json()
        suggestions = body.get('meetingTimeSuggestions', [])

        # server 側で “全員 Free” を二重チェック
        def all_free(sug):
            av = sug.get('attendeeAvailability', [])
            for a in av:
                availability = (a.get('availability') or '').lower()
                attendee_type = (a.get('attendee', {}).get('type') or 'required').lower()
                if attendee_type == 'required' and availability != 'free':
                    return False
            return True

        strict_slots = [s for s in suggestions if all_free(s)]
        if strict_slots:
            return jsonify({
                "success": True,
                "slots": strict_slots,
                "emptySuggestionsReason": body.get('emptySuggestionsReason')
            })

        # 2) 推薦で出なかった／件名補正が必要 → 在宅件名を Busy から除外して自前で探索
        slots = _find_times_subject_aware(
            headers=headers,
            attendees=attendees_from_request,
            start_local=start_local,
            end_local=end_local,
            win_tz=user_timezone,
            duration_min=duration_minutes,
            ignore_titles=ignore_titles,
            room_email=room_email
        )
        return jsonify({
            "success": True,
            "slots": slots,
            "emptySuggestionsReason": body.get('emptySuggestionsReason')
        })

    except requests.exceptions.HTTPError as e:
        return _handle_graph_http_error(e)
    except Exception as e:
        return jsonify({"success": False, "error": f"サーバーエラー: {str(e)}"}), 500


# ---- 予定作成（Teams/会議室対応 & 確認はフロントで実施）----
@meeting_scheduler_bp.route('/api/meetings/create_event', methods=['POST'])
def create_event():
    """
    予定作成API（後方互換あり）:
      - 旧: start/end (UTC文字列, e.g., "2025-09-05T03:00:00Z")
      - 新: startLocal/endLocal + timeZone (IANA でも Windows でも可)
        → サーバ側で Windows TZ に正規化して UTC(Z) へ変換して保存

    追加仕様:
      - isOnlineMeeting が True で onlineMeetingProvider 未指定なら
        "teamsForBusiness" を既定補完
      - roomEmail があれば attendees(type=resource) に追加し、location にも反映
    """
    headers = get_auth_header()
    if not headers:
        return jsonify({"success": False, "error": "認証トークンが見つかりません。"}), 401

    data = request.get_json(silent=True) or {}
    subject = data.get('subject') or '会議'
    body_html = data.get('body') or 'AIアシスタントによって設定されました。'

    # 受け取り形式（旧/新）を両対応
    start_utc = data.get('start')     # 旧: UTC 文字列
    end_utc   = data.get('end')
    start_local = data.get('startLocal')  # 新: ローカル文字列 "YYYY-MM-DDTHH:MM:SS"
    end_local   = data.get('endLocal')
    tz_in       = data.get('timeZone')    # IANA でも Windows でもOK

    attendees_in = data.get('attendees', [])
    is_online = bool(data.get('isOnlineMeeting'))
    provider = data.get('onlineMeetingProvider')
    room_email = (data.get('roomEmail') or '').strip()

    # 入力バリデーション → UTC(Z) に正規化
    if (not start_utc or not end_utc) and (not start_local or not end_local):
        return jsonify({"success": False, "error": "開始・終了日時が指定されていません。"}), 400

    # 新形式が来たらサーバ側で UTC(Z) 化
    if start_local and end_local:
        win_tz = to_windows_tz(tz_in, default='Tokyo Standard Time')
        start_utc = _local_to_utc_z(start_local, win_tz)  # "YYYY-MM-DDTHH:MM:SSZ"
        end_utc   = _local_to_utc_z(end_local,   win_tz)

    # ここまでで start_utc / end_utc は必ず埋まっているはず
    if not start_utc or not end_utc:
        return jsonify({"success": False, "error": "開始・終了日時（UTC）が指定されていません。"}), 400

    # 参加者整形
    attendees = [
        {"emailAddress": {"address": str(email).strip()}, "type": "required"}
        for email in attendees_in if email
    ]
    location_obj = None
    if room_email:
        attendees.append({
            "emailAddress": {"address": room_email},
            "type": "resource"
        })
        location_obj = {"displayName": room_email}

    # 要求ボディ
    request_body = {
        "subject": subject,
        "body": {"contentType": "HTML", "content": body_html},
        "start": {"dateTime": start_utc, "timeZone": "UTC"},
        "end": {"dateTime": end_utc, "timeZone": "UTC"},
        "attendees": attendees,
        "isOnlineMeeting": is_online,
    }

    # Teams 既定補完
    if is_online:
        request_body["onlineMeetingProvider"] = provider or "teamsForBusiness"

    if location_obj:
        request_body["location"] = location_obj

    try:
        # TOCTOU 対策: 予定作成直前に参加者全員の空き時間を再確認
        check_emails = [a["emailAddress"]["address"] for a in attendees]
        conflict_emails = _check_slot_conflicts(headers, check_emails, start_utc, end_utc)
        if conflict_emails:
            return jsonify({
                "success": False,
                "error": (
                    f"選択した時間帯はすでに予約済みです"
                    f"（{', '.join(conflict_emails)}）。"
                    f"候補を再検索してください。"
                )
            }), 409

        resp = requests.post(f"{GRAPH_API_ENDPOINT}/me/events", headers=headers, json=request_body)
        resp.raise_for_status()
        return jsonify({"success": True, "event": resp.json()})
    except requests.exceptions.HTTPError as e:
        return _handle_graph_http_error(e)
    except Exception as e:
        return jsonify({"success": False, "error": f"サーバーエラー: {str(e)}"}), 500


# =========================
# 自前フォールバック（在宅件名＝Free扱い）
# =========================

def _parse_local_naive(iso_local: str) -> datetime:
    # "YYYY-MM-DDTHH:MM:SS" を naive datetime として解釈
    return datetime.fromisoformat(iso_local)

def _local_to_utc_z(iso_local: str, win_tz: str) -> str:
    # 簡易に Windows TZ の固定オフセット（DST 無視）で UTC に換算
    off = tz_offset_hours(win_tz)
    dt_local = _parse_local_naive(iso_local)
    dt_utc = dt_local - timedelta(hours=off)
    return dt_utc.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")

def _merge_intervals(intervals):
    if not intervals:
        return []
    s = sorted(intervals, key=lambda x: x[0])
    merged = [s[0]]
    for cur in s[1:]:
        if cur[0] <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], cur[1]))
        else:
            merged.append(cur)
    return merged

def _invert_intervals(busy, start, end):
    if not busy:
        return [(start, end)]
    res = []
    cur = start
    for b in busy:
        if b[0] > cur:
            res.append((cur, b[0]))
        cur = max(cur, b[1])
        if cur >= end:
            break
    if cur < end:
        res.append((cur, end))
    return res

def _fetch_busy_calendarview(headers, principal: str, start_z: str, end_z: str, ignore_titles):
    """calendarView で subject/ showAs を参照し Busy を作る。ページネーション対応（全件取得）。"""
    # principal が自分でも /users/{principal}/... に統一（/me でもよいが挙動統一のため）
    url = f"{GRAPH_API_ENDPOINT}/users/{principal}/calendarView"
    params = {
        "startDateTime": start_z,
        "endDateTime": end_z,
        "$select": "subject,showAs,start,end",
        "$orderby": "start/dateTime",
        "$top": "250"  # Graph API calendarView の 1 ページ上限
    }
    # @odata.nextLink を辿って全ページ取得（100件超のカレンダーへの対応）
    items = []
    next_url = url
    first_page = True
    while next_url:
        if first_page:
            r = requests.get(next_url, headers=headers, params=params)
            first_page = False
        else:
            r = requests.get(next_url, headers=headers)
        r.raise_for_status()
        body = r.json()
        items.extend(body.get('value', []))
        next_url = body.get('@odata.nextLink')  # 次ページなければ None

    busy = []
    for ev in items:
        subj = (ev.get('subject') or '').strip()
        show_as = (ev.get('showAs') or '').lower()
        if any(k in subj for k in ignore_titles):
            continue  # 在宅等の件名は Busy から除外
        if show_as == 'free':
            continue  # Free は除外
        s = datetime.fromisoformat(ev['start']['dateTime'])
        e = datetime.fromisoformat(ev['end']['dateTime'])
        if e > s:
            busy.append((s, e))
    return _merge_intervals(busy)

def _fetch_busy_getschedule(headers, principal: str, start_local: str, end_local: str, win_tz: str):
    """getSchedule（件名なし）で Busy を取得（calendarView が権限等で失敗したときのフォールバック）。"""
    url = f"{GRAPH_API_ENDPOINT}/users/{principal}/calendar/getSchedule"
    body = {
        "schedules": [principal],
        "startTime": {"dateTime": start_local, "timeZone": win_tz},
        "endTime": {"dateTime": end_local, "timeZone": win_tz},
        "availabilityViewInterval": 30
    }
    r = requests.post(url, headers=headers, json=body)
    r.raise_for_status()
    v = r.json().get('value', [])
    busy = []
    if v:
        items = v[0].get('scheduleItems', [])
        for it in items:
            status = (it.get('status') or '').lower()
            # 厳格に：tentative も Busy とみなす（“全員 Free 限定”のため）
            if status in ('busy', 'oof', 'workingelsewhere', 'tentative'):
                s = it['start']['dateTime']
                e = it['end']['dateTime']
                sdt = datetime.fromisoformat(s)
                edt = datetime.fromisoformat(e)
                if edt > sdt:
                    busy.append((sdt, edt))
    return _merge_intervals(busy)

def _check_slot_conflicts(headers, attendee_emails: list, start_z: str, end_z: str) -> list:
    """
    指定の UTC 時間帯に Busy な参加者のメールアドレスリストを返す。
    空リストは競合なし（全員空き）を意味する。
    権限不足でカレンダーを参照できない参加者はスキップする。
    """
    conflicts = []
    for email in attendee_emails:
        if not email:
            continue
        try:
            url = f"{GRAPH_API_ENDPOINT}/users/{email}/calendarView"
            params = {
                "startDateTime": start_z,
                "endDateTime": end_z,
                "$select": "subject,showAs",
                "$top": "5"  # 1 件でも Busy があれば NG なので少数で足りる
            }
            r = requests.get(url, headers=headers, params=params)
            r.raise_for_status()
            events = r.json().get('value', [])
            for ev in events:
                show_as = (ev.get('showAs') or '').lower()
                if show_as != 'free':
                    conflicts.append(email)
                    break
        except requests.exceptions.HTTPError:
            # 権限不足等でチェックできない場合はスキップ（作成処理は続行）
            pass
    return conflicts


def _find_times_subject_aware(headers, attendees, start_local, end_local, win_tz, duration_min, ignore_titles, room_email):
    # 検索窓（UTC Z 文字列）を作成
    start_z = _local_to_utc_z(start_local, win_tz)
    end_z = _local_to_utc_z(end_local, win_tz)

    # 参加者ごとの Busy（在宅件名は除外）
    all_free = []
    for email in attendees:
        try:
            busy = _fetch_busy_calendarview(headers, email, start_z, end_z, ignore_titles)
        except requests.exceptions.HTTPError:
            # 権限不足など → getSchedule にフォールバック（件名は見えないが Free/Busy は得られる）
            busy = _fetch_busy_getschedule(headers, email, start_local, end_local, win_tz)
        free = _invert_intervals(busy,
                                 datetime.fromisoformat(start_z.replace("Z", "+00:00")).replace(tzinfo=None),
                                 datetime.fromisoformat(end_z.replace("Z", "+00:00")).replace(tzinfo=None))
        all_free.append(free)

    # 会議室の可用性も交差に含める（roomEmail 指定時）
    if room_email:
        try:
            rbusy = _fetch_busy_calendarview(headers, room_email, start_z, end_z, ignore_titles=[])
        except requests.exceptions.HTTPError:
            rbusy = _fetch_busy_getschedule(headers, room_email, start_local, end_local, win_tz)
        rfree = _invert_intervals(rbusy,
                                  datetime.fromisoformat(start_z.replace("Z", "+00:00")).replace(tzinfo=None),
                                  datetime.fromisoformat(end_z.replace("Z", "+00:00")).replace(tzinfo=None))
        all_free.append(rfree)

    # 共通空き = 全員の空きの交差
    if not all_free:
        return []
    common = all_free[0]
    for free in all_free[1:]:
        new_common = []
        for a in common:
            for b in free:
                s = max(a[0], b[0])
                e = min(a[1], b[1])
                if e > s:
                    new_common.append((s, e))
        common = new_common
        if not common:
            break

    # 所要時間を満たす候補（15分刻み、最大10件）
    step = 15
    dur = timedelta(minutes=duration_min)
    out = []
    for (s, e) in common:
        cur = s.replace(second=0, microsecond=0)
        # 15分丸め
        minute = (cur.minute // step) * step
        cur = cur.replace(minute=minute)
        while cur + dur <= e:
            out.append({
                "meetingTimeSlot": {
                    "start": {"dateTime": cur.replace(microsecond=0).isoformat(), "timeZone": win_tz},
                    "end":   {"dateTime": (cur + dur).replace(microsecond=0).isoformat(), "timeZone": win_tz}
                }
            })
            if len(out) >= 10:
                break
            cur += timedelta(minutes=step)
        if len(out) >= 10:
            break
    return out
