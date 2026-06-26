import io
import re
import logging
import json
import requests
import base64
import os
from functools import wraps
from datetime import datetime, timezone
from urllib.parse import urlparse, unquote, quote 
from flask import Blueprint, request, jsonify, render_template
from azure.cosmos import exceptions as cosmos_exceptions
from .utils.gemini import generate_with_gemini
from .utils.sharepoint_storage import (
    save_sharepoint_tool, get_sharepoint_tool, 
    list_user_tools, delete_sharepoint_tool, rename_sharepoint_tool,
    check_and_update_token_usage,
    toggle_interaction, get_public_tools, get_user_favorite_tools,
    get_tool_cosmos_container, TOOL_CATEGORIES,
    get_tool_interaction_status,increment_tool_view_count,
    list_all_tools_for_admin
)
from .utils.file_processor import extract_file_content_to_part
import config

logger = logging.getLogger(__name__)
sharepoint_tool_bp = Blueprint('sharepoint_tool', __name__)

categories_str = ", ".join(TOOL_CATEGORIES)

SHAREPOINT_GEN_SYSTEM_PROMPT = f"""
あなたはSharePoint、OneDrive、Microsoft Graph API、およびM365エコシステムのエキスパートです。
ユーザーの要望に基づき、業務を自動化するSPA(Single Page Application)ツールを作成してください。

出力は必ず以下の構造を持つJSONのみを返してください。
{{
  "toolName": "ツールの名称（具体的で魅力的な名称）",
  "category": "以下の【有効なカテゴリ】から、最も適切なものを1つだけ正確に選択してください。リストにない分類は絶対に使用禁止です。",
  "description": "Markdown形式のツール詳細仕様書。以下の構成で記述すること。
    【記述上の厳格ルール】
    - セクション間には必ず2行の改行を入れること。
    - リストの前後に必ず空行を入れ、行頭は '- ' を使用すること。
    - 表形式（Table）を積極的に活用すること。
    
    ### 1. 概要
       - 解決する業務課題 / 導入目的と解決策
    ### 2. 機能一覧
    ### 3. 入出力定義（詳細な表形式）
    ### 4. 処理ロジック（ステップバイステップで記述）
    ### 5. エラーハンドリング・API利用概要",
  "ui": "Tailwind CSSを使用したHTML（body内のみ。モダンで使いやすいUI）",
  "logic": "JavaScript（script内のみ）"
}}

【有効なカテゴリ】
{categories_str}

### デザイン・UI実装ガイドライン（Golden Sample）
生成するツールは、以下のモダンなレイアウトと配色を基本としてください：
1. **レイアウト**: 
   - ヘッダー（タイトル・ステータス）、左固定サイドバー（フィルタ・設定）、右メインコンテンツ（リスト・結果）、下部またはオーバーレイで詳細パネル。
2. **配色（Tailwind）**: 
   - ベース: `bg-slate-50 dark:bg-slate-950`
   - テキスト: `text-slate-900 dark:text-slate-100`
   - アクセント: `indigo-600`, `emerald-500` 等を使用し、高級感を持たせる。
3. **ローディング表示**: 
   - 処理中は必ず `id="loading"` のオーバーレイ（`fixed inset-0 bg-slate-900/60 ... animate-spin`）を表示し、ユーザー操作をブロックすること。
4. **フィードバック**: 
   - 成功/失敗は `alert` だけでなく、画面内のステータス表示やトースト通知を活用すること。

### JavaScript実装の最重要ルール
1. 【関数定義】windowオブジェクトに登録すること（例：`window.search = ...`）。
2. 【Graph API利用】`await window.callGraphProxy(endpoint, method, payload)` を使用すること。
3. 【AI解析】`const result = await window.callToolAI(prompt, itemId, driveId, systemInstruction);` を使用すること。
   - **重要：`systemInstruction` 引数には、その機能固有の役割（例：「あなたは翻訳家です」等）を必ず文字列として定義して渡してください。**
   - 複数のAI機能がある場合は、それぞれの関数内で異なる `systemInstruction` を定義してください。
   - ファイル内容をAIに分析させる場合（要約・翻訳・分析等）、必ず `itemId` と `driveId` を渡すこと。省略するとファイル名のみがAIに届きます。
     例: `await window.callToolAI("要約してください", file.id, DRIVE_ID, "あなたは要約の専門家です");`
4. 【結果表示（Markdown対応）】
   - 結果を表示するHTML要素（div等）には、必ずクラス `class="prose dark:prose-invert max-w-none"` を付与すること。
   - これにより、箇条書き( - )が点(●)として表示され、見出しや余白が正しく装飾されます。
   - 表示時は必ず `marked.parse(result)` を使用してください。
5. 【エラーハンドリング】必ず try-catch を使い、alert等でユーザーに通知すること。
6. 【初期化処理】**window.onloadやDOMContentLoadedは絶対に使用禁止。** 
   スクリプトの最後で初期化関数（例：`init();`）を直接呼び出すこと。
7. 【スタイル操作】Tailwindの動的クラス（`ml-${{depth}}`等）は不可。`el.style.paddingLeft` 等で直接操作すること。
8. 【高度処理連携】
   - Python環境が利用可能な場合、`const result = await window.runPython(code, context);` が使用可能です。
   - **重要: Python側でGraph APIを利用する場合（ファイルのDL/ULなど）、認証トークンが必要です。**
   - 以下の手順でトークンを取得し、必ず context に含めてください：
     ```javascript
     const token = await window.getAuthToken();
     const context = {{
         accessToken: token, // ★必須
         driveId: DRIVE_ID,
         itemId: itemId,
         ...
     }};
     const result = await window.runPython(null, context);
     ```
9. 【SharePointリストの添付ファイル】
   - リストアイテムの添付ファイルを「ブラウザで直接開く」リンクを作成する場合、必ず `await window.getAttachmentDirectUrl(itemId, fileName)` を使用してください。
   - 例: 
     ```javascript
     const url = await window.getAttachmentDirectUrl(item.id, attachment.name);
     window.open(url, '_blank');
     ```
   - 添付ファイルの一覧自体は `GET /sites/${{SITE_ID}}/lists/${{LIST_ID}}/items/${{itemId}}/attachments` で取得できます。
     
### Pythonコード実装の鉄則（重要）
1. **戻り値のルール**: 
   - Pyodideの仕様上、`try...except` ブロックの中にある変数はJSに戻りません。
   - **必ずスクリプトの最終行（インデントなし）で、戻したい変数名のみを記述してください。**
   - 良い例:
     ```python
     result = None
     try:
         result = {{"status": "success", "data": "..."}}
     except:
         result = {{"status": "error"}}
     result  # ← 必ず最後に変数を置く
     ```
2. **バイナリ処理**: Base64でやり取りし、`base64.b64decode(context['fileBase64'])` で取得すること。
3. **JS側の判定**: Pythonからの戻り値は、文字列だけでなくオブジェクト（辞書）の場合もあることを考慮してロジックを組んでください。

### リソース・コンテキストの利用ルール (★重要)
以下のグローバル定数が実行環境に自動注入されます。
**ハードコーディングは避け、必ずこれらの定数を使用してください。**

1. **SharePoint / OneDrive**
   - `SITE_ID`: 対象サイトID
   - `DRIVE_ID`: 対象ドライブ(ライブラリ)ID
   - `LIST_ID`: 対象リストID (リスト操作時)
   - `TARGET_FOLDERS`: 対象フォルダ名の配列 (例: `["見積書", "請求書"]`)
     - コード例: `const targetName = TARGET_FOLDERS[0];`

2. **Outlook**
   - `MAIL_FOLDER_IDS`: 対象メールフォルダIDの配列
     - コード例: `for (const fid of MAIL_FOLDER_IDS) {{ ... }}`

3. **Planner / To Do**
   - `PLAN_IDS`: 対象PlannerプランIDの配列
   - `TODO_LIST_IDS`: 対象To DoリストIDの配列
     - コード例: `for (const listId of TODO_LIST_IDS) {{ ... }}`

4. **Teams**
   - `TEAM_IDS`: 対象チームIDの配列
   - `CHANNEL_IDS`: 対象チャネルIDの配列
     - コード例: `for (const chId of CHANNEL_IDS) {{ ... }}`
   - `TARGET_MEETINGS`: 対象会議情報の配列 (オブジェクト: `{{ title: "...", joinUrl: "...", start: "..." }}`)
     - **重要**: 会議関連APIには `meetingId` が必要です。`joinUrl` しかない場合は以下のパターンを厳守すること:
       ```javascript
       // getMeetingIdFromUrl は失敗時に自動的に throw するため、必ず try-catch で囲むこと
       const meetingId = await window.getMeetingIdFromUrl(event.onlineMeeting.joinUrl);
       ```
       この関数はログインユーザーが主催者でない場合・権限不足の場合に例外をスローします。


### 追加API利用ルール (Microsoft Graph 拡張パターン)
1. **SharePoint / OneDrive (File & List)**
   - **SharePointサイト内**: `/sites/${{SITE_ID}}/...`
   - **ライブラリ/フォルダ内**: `/drives/${{DRIVE_ID}}/items/${{itemId}}/children`
   - **OneDrive (個人)**: `/me/drive/root/children`
   - **全社検索 (Microsoft Search)**: `POST /search/query` (entityTypes: ['driveItem'])

2. **Outlook (Mail/Calendar)**
   - **メール取得**: `GET /me/messages` (filter/search活用)
   - **メール作成 (推奨)**: `window.location.href = "mailto:..."` 
   - **カレンダー**: `GET /me/calendar/events`, `POST /me/calendar/events`

3. **Planner (Tasks - Group)**
   - **プラン/タスク**: `GET /me/planner/plans`, `POST /planner/tasks`
   - **注意**: タスク作成時は `planId`, `bucketId`, `title` が必須。

4. **Microsoft To Do (Tasks - Personal)**
   - **リスト一覧**: `GET /me/todo/lists`
   - **タスク一覧**: `GET /me/todo/lists/${{listId}}/tasks`
   - **タスク作成**: `POST /me/todo/lists/${{listId}}/tasks`
   - **注意**: `TODO_LIST_IDS` が指定されている場合は、それらをループして処理してください。指定がない場合はデフォルトのタスクリストを使用してください。

5. **Teams (Advanced & Communication)**
   - **【重要】`$select` 非対応エンドポイント一覧**: 以下のエンドポイントは `$select` クエリパラメータをサポートしません。使用すると `Query option 'Select' is not allowed` エラーが発生します。**絶対に `$select` を付けないこと。**
     - `GET /me/onlineMeetings/{{meetingId}}` — `$select` 禁止
     - `GET /shares/{{encodedUrl}}/driveItem` — `$select` 禁止
   - **チーム/チャネル一覧**: `GET /teams/${{teamId}}/channels` (Channel.ReadBasic.All)
   - **チャネルメッセージ取得**: `GET /teams/${{teamId}}/channels/${{channelId}}/messages`
   - **チャネルへの投稿**: `POST /teams/${{teamId}}/channels/${{channelId}}/messages` (ChatMessage.Send)
     - payload: {{ "body": {{ "content": "Hello World" }} }}
   - **会議トランスクリプト (Meeting Transcripts)**:
     - **【アクセス制限】**: トランスクリプトAPIはログインユーザーが**主催者**の会議のみ対象です。参加者の会議は取得できません。
     - 手順1 (会議一覧取得): `GET /me/calendar/events?$top=100&$orderby=start/dateTime desc` で全件取得後、**クライアント側で** `isOnlineMeeting === true` および `new Date(e.end.dateTime) < new Date()` でフィルタリングすること。**`$filter` と `$orderby` の同時使用は禁止** — Graph API が HTTP 400 を返す既知の制限のため。
     - 手順2 (会議ID解決): ヘルパー関数 `window.getMeetingIdFromUrl(event.onlineMeeting.joinUrl)` を使用。主催者でない場合は例外をスローするため必ず try-catch で囲むこと。
     - 手順3 (リスト取得): `GET /me/onlineMeetings/${{meetingId}}/transcripts`
     - 手順4 (コンテンツ取得): `GET .../transcripts/${{transcriptId}}/content?$format=text/vtt`
     - **注意**: トランスクリプト本文を取得したら、`window.callToolAI` に渡して要約させるのが効果的です。

   - **会議チャットのファイル要約 (Meeting Chat File Summarization)**:
     - **概要**: Teamsの会議には専用のチャットスレッドがあり、参加者が共有したファイルを一覧取得してAIで要約できます。
     - **【アクセス制限】**: chatId取得のために `getMeetingIdFromUrl` を使用するため、ログインユーザーが**主催者**の会議のみ対象です。
     - 手順1 (会議のchatId取得): meetingIdを取得後、オンライン会議情報からchatIdを得る。
       ```javascript
       // getMeetingIdFromUrl は失敗時に throw するため、必ず try-catch で囲むこと
       const meetingId = await window.getMeetingIdFromUrl(meeting.onlineMeeting.joinUrl);
       // ⚠️ /me/onlineMeetings/{id} は $select をサポートしないため、クエリパラメータなしで呼ぶこと
       const meetingInfo = await window.callGraphProxy(`/me/onlineMeetings/${{meetingId}}`);
       const chatId = meetingInfo.chatInfo.threadId;
       ```
     - 手順2 (チャットメッセージからファイル添付を列挙):
       ```javascript
       const messagesRes = await window.callGraphProxy(
         `/chats/${{chatId}}/messages?$top=50&$select=attachments,createdDateTime,from`
       );
       const fileAttachments = [];
       for (const msg of (messagesRes.value || [])) {{
         for (const att of (msg.attachments || [])) {{
           // contentType が 'reference' のものがファイル共有（リンク共有は除外）
           if (att.contentType === 'reference' && att.contentUrl) {{
             fileAttachments.push({{
               name: att.name,
               contentUrl: att.contentUrl,
               sharedAt: msg.createdDateTime
             }});
           }}
         }}
       }}
       ```
     - 手順3 (contentUrl → driveId/itemId の解決): Microsoft Graph の共有URLデコードAPIを使用する。
       ```javascript
       async function resolveShareUrl(contentUrl) {{
         // base64url エンコード: "u!" プレフィックス + base64url
         const encoded = 'u!' + btoa(unescape(encodeURIComponent(contentUrl)))
                           .replace(/=/g, '').replace(/\\+/g, '-').replace(/\\//g, '_');
         // ⚠️ /shares/{id}/driveItem は $select をサポートしない。クエリパラメータなしで呼ぶこと
         const item = await window.callGraphProxy(`/shares/${{encoded}}/driveItem`);
         return {{ itemId: item.id, driveId: item.parentReference.driveId }};
       }}
       ```
       - **注意**: 権限不足や外部共有ファイルで失敗する場合があるため、必ず try-catch で囲み、失敗したファイルはスキップして処理を継続すること。
     - 手順4 (AIで要約): 解決した itemId/driveId を `callToolAI` に渡す。
       ```javascript
       const {{ itemId, driveId }} = await resolveShareUrl(att.contentUrl);
       const summary = await window.callToolAI(
         "このファイルの内容を日本語で要約してください。重要なポイントを箇条書きでまとめてください。",
         itemId,
         driveId,
         "あなたはビジネス文書の要約の専門家です。会議で共有されたファイルの内容を簡潔かつ正確に要約してください。"
       );
       ```
     - **実装上の注意**:
       - 手順3が失敗したファイルには「このファイルは直接アクセスできません」と表示してスキップする。
       - 要約結果は `marked.parse(summary)` で描画し、`class="prose dark:prose-invert max-w-none"` を付与すること。
       - ファイル種別（PDF/Word/Excel等）はファイル名の拡張子から判定してアイコンを切り替えると良い。

### 【最重要】Teams連携・送信機能の厳格な実装パターン
Teamsへのメッセージ送信機能が求められた場合、誤送信防止のため、**直接Graph APIを叩くことは禁止です。**
必ず以下の**安全なラッパー関数**を使用してください。この関数は自動的に送信先選択・確認ポップアップを表示します。

#### 送信関数の使用ルール
```javascript
// content: 送信したいメッセージ本文（HTML/Markdown可）
// attachments: (任意) Base64エンコードされたファイル配列など
await window.sendTeamsMessage(content);
```

**禁止事項:**
- `POST /chats/...` や `POST /teams/...` を直接 `callGraphProxy` で呼ばないこと。
- 送信先ID（chatId, channelId）をコード内にハードコーディングしないこと（送信先は実行時にユーザーがポップアップで決定します）。

### 【重要】名前解決（Name Resolution）のルール
「○○フォルダ」「○○プラン」「○○チャネル」など名前で指定された場合、必ず以下の手順をコードに含めてください：
1. 一覧取得APIを呼ぶ。
2. ループ処理で `displayName` または `name` が一致するものを探す。
3. 見つかった `id` を使って後続処理を行う。
4. 見つからない場合は `alert('○○が見つかりませんでした')` と表示して終了する。

### 【重要】`callGraphProxy` のエラー動作と「ファイル存在確認」パターン
- `callGraphProxy` は HTTP 4xx / 5xx のレスポンスに対して **常に例外をスローします**（`null` や `undefined` は返しません）。
- **「ファイルが存在するか確認し、なければ作成する」処理では、以下のパターンを必ず使用してください。**
  存在しない場合に Graph API は 404 を返すため、`callGraphProxy` が throw します。
  `if (res && res.id) {{}} else {{}}` の `else` ブランチには 404 時には**絶対に到達しません**。

#### 【絶対禁止】XLSX ファイルを Base64 文字列としてコード内に埋め込むことは禁止です。
LLM が生成する Base64 は ZIP 構造としてはそれらしく見えても、内部の XML コンテンツが常に空になり
Excel Online が `FileCorruptTryRepair` を返します。どのモデルが生成しても例外なく破損ファイルになります。

#### 新規 Excel ファイルの作成は専用 API を使うこと
バックエンドの `/api/sharepoint/create-empty-excel` エンドポイントが `openpyxl` で有効なファイルを生成・アップロードします。

```javascript
// ✅ 正しいパターン: try-catch で 404 (itemNotFound) を「未存在」として正常系で扱う
let fileItemId = null;
try {{
    const res = await window.callGraphProxy(`/me/drive/root:/${{folderName}}/${{fileName}}`);
    if (res && res.id) fileItemId = res.id;
}} catch (e) {{
    // itemNotFound / 404 は「ファイルが存在しない」という正常な状態なので再スローしない
    if (!e.message.includes('itemNotFound') && !e.message.includes('404')) throw e;
}}
if (!fileItemId) {{
    // ✅ 新規Excelファイル作成: 専用APIを呼ぶ（Base64埋め込み禁止）
    // driveId: OneDrive個人="me", SharePoint=DRIVE_ID 定数を渡す
    const createRes = await fetch('/api/sharepoint/create-empty-excel', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ folder: folderName, filename: fileName, driveId: DRIVE_ID }})
    }});
    if (!createRes.ok) {{
        const err = await createRes.json().catch(() => ({{}}));
        throw new Error(err.error || `ファイル作成失敗: HTTP ${{createRes.status}}`);
    }}
    const created = await createRes.json();
    fileItemId = created.id;
}}
```
- **絶対に禁止のアンチパターン:**
```javascript
// ❌ 誤り1: callGraphProxy は 404 でも throw するため、else に到達しない
const searchRes = await window.callGraphProxy(url);
if (searchRes && searchRes.id) {{ ... }}
else {{ createFile(); }}  // ← 404 時はここに絶対に来ない

// ❌ 誤り2: LLM生成の Base64 を埋め込んで PUT でアップロードする（必ず FileCorruptTryRepair になる）
const XLSX_BASE64 = "UEsD..."; // ← 禁止
```

### ファイル種別ごとの編集・操作（Microsoft Graph）ルール

1. **Excel (.xlsx)**:
   - **テーブル操作**: `.../workbook/tables` でテーブル取得、`POST .../rows` で行追加。
   - **セルの読み書き**: `PATCH .../worksheets/${{sheet}}/range(address='A1')` (payload: {{ values: [[val]] }})
   - **ピボット作成**: `POST .../worksheets/${{sheet}}/pivotTables/add` を使用。
   - **【書き込み操作は必ずセッション確立が必要】**: Workbook APIで書き込む場合は以下の手順を厳守すること。
     1. `POST .../workbook/createSession` {{ "persistChanges": true }} → `sessionId` を取得。
     2. 以降の書き込みリクエストヘッダーに `workbook-session-id: <sessionId>` を付与。
     3. 書き込み完了後に `POST .../workbook/closeSession` でセッションを閉じる。
     ※ セッション未確立のまま書き込むと `423 Locked` / `409 Conflict` エラーが発生します。
   - ※ Excel APIは「ファイル内にテーブル定義」があると非常に安定します。

2. **Word (.docx) / PowerPoint (.pptx)**:
   - **メタデータ更新**: `PATCH /drives/${{DRIVE_ID}}/items/${{itemId}}` でタイトルやカスタムプロパティを更新。
   - **内容の置換**: 基本的に「ファイルをダウンロード(GET content) → JS/AIで加工 → アップロード(PUT content)」のフローをとります。
   - **PDF変換**: `GET /drives/${{DRIVE_ID}}/items/${{itemId}}/content?format=pdf` で変換後のバイナリが取得可能。

3. **テキスト (.txt) / CSV (.csv) / Markdown (.md)**:
   - **内容の更新**: 
     - 取得: `await window.callGraphProxy('/drives/${{DRIVE_ID}}/items/${{itemId}}/content', 'GET')`
     - 保存: `await window.callGraphProxy('/drives/${{DRIVE_ID}}/items/${{itemId}}/content', 'PUT', textData, {{ "Content-Type": "text/plain" }})`
   - AIに「中身を読み取って、特定箇所を書き換えて、PUTで保存して」と指示するのが有効。

4. **画像 (.png, .jpg) / 動画 (.mp4)**:
   - **解析**: AI(`window.callToolAI`)に画像ファイルを渡し、OCR（文字起こし）や内容解説を行わせる。
   - **整理**: 解析結果を元に `PATCH` でファイル名をリネームしたり、フォルダを移動させる。
   - **サムネイル**: `GET /drives/${{DRIVE_ID}}/items/${{itemId}}/thumbnails` でプレビューを取得。

5. **全般的なファイル操作**:
   - **移動**: `PATCH` で `parentReference: {{ id: targetFolderId }}` を指定。
   - **コピー**: `POST .../copy` (payload: {{ parentReference: {{ id: targetFolderId }} }})。

### 【重要】バイナリデータ処理の厳格ルール

#### 画像ファイル (.png, .jpg 等)
1. ダウンロード: `const fileBase64 = await window.callGraphProxy(url, 'GET');` を使用。
2. Pythonへの渡し方: `window.runPython(code, {{ fileBase64: fileBase64, width: 800 }});` のようにキー名を `fileBase64` に固定すること。
3. Python内での処理:
   - 冒頭で必ず `await micropip.install('Pillow')` を実行。
   - `base64.b64decode(context['fileBase64'])` で取得。
   - 最後に必ず `base64.b64encode(buf.getvalue()).decode()` した文字列を返す。
4. アップロード: `await window.uploadBinary(url, resultBase64, 'image/jpeg');` を使用。

#### Excel ファイル (.xlsx 等)
1. ダウンロード: `const fileBase64 = await window.callGraphProxy(url, 'GET');` を使用 (callGraphProxyはBase64文字列を返す)。
2. Pythonへの渡し方: `window.runPython(code, {{ fileBase64: fileBase64, fileName: fileName }});` のようにキー名を `fileBase64` に固定すること。
3. Python内での処理:
   - 冒頭で必ず `await micropip.install('openpyxl')` を実行。
   - `import base64, io; wb_bytes = base64.b64decode(context['fileBase64'])` でデコード。
   - `import openpyxl; wb = openpyxl.load_workbook(io.BytesIO(wb_bytes))` で読み込み。
4. アップロード: `await window.uploadBinary(url, resultBase64, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');` を使用。
"""

PYTHON_GEN_SYSTEM_PROMPT = """
あなたはブラウザ上のWebAssembly (Pyodide) 環境で動作するPythonコードのエキスパートです。
ユーザーの要望と、連携するJavaScriptツールの仕様に基づき、データ処理を行うPythonコードを作成してください。

【最重要：ライブラリインストールの鉄則】
Pyodide環境では、標準ライブラリ以外は **使用前に必ずインストール** が必要です。
コードの冒頭には、以下の対応表に基づき、必ず `await micropip.install()` ブロックを記述してください。

### 📦 ライブラリ対応表 (キーワード -> 必須コード)
1. **Excel (.xlsx)** 処理の場合:
   ```python
   import micropip
   await micropip.install("pandas")
   await micropip.install("openpyxl") 
   # xlsx書き込みには openpyxl が必須です
   import pandas as pd
   import openpyxl
   ```
2. **Word (.docx)** 処理の場合:
   ```python
   import micropip
   await micropip.install("python-docx")
   import docx
   ```
3. **PowerPoint (.pptx)** 処理の場合:
   ```python
   import micropip
   await micropip.install("python-pptx")
   from pptx import Presentation
   ```
4. **画像処理 (Image)** の場合:
   ```python
   import micropip
   await micropip.install("Pillow")
   from PIL import Image
   import io
   ```
5. **数値計算・データ分析** の場合:
   ```python
   import micropip
   await micropip.install("numpy")
   await micropip.install("scikit-learn") # 必要であれば
   ```

【重要：エラー対応ルール】
1. **ModuleNotFoundError 絶対回避**: 上記対応表にある機能を使う際は、インポート文を書く前に必ず `install` を書いてください。
2. **UnicodeDecodeError**: base64データを扱う際は `base64.b64decode(data).decode('utf-8', errors='replace')` を使用してください。

【コード構造の鉄則】
- **必ずPythonコードブロック（```python ... ```）の中にコードを記述してください。**
- JavaScriptから渡されるデータは `globals().get('context')` で取得してください。
- 最後に必ず評価したい変数（結果）を最終行に記述してください（`print` ではなく、変数を置く）。


【制約事項: Pyodide環境の限界】
1. **動画・音声処理は禁止**: `ffmpeg` や `cv2.VideoCapture` は動作しません。これらはJavaScript側で処理される前提とし、Python側では「処理結果の集計・レポート生成」のみを行ってください。
2. **画像処理は推奨**: `Pillow` (PIL) は利用可能です。
3. **外部通信**: `requests` は使えません。`pyodide.http.pyfetch` を使用してください。
4. **出力**: 最終行の評価値がJavaScriptに戻ります。

【利用可能なライブラリ】
- データ分析: pandas, numpy, scipy, tabulate (micropipでインストール) 
- 機械学習: scikit-learn (micropipでインストール)
- 時系列分析: statsmodels (micropipでインストール)
- 画像処理: Pillow (micropipでインストール)
- オフィス系: openpyxl, python-pptx, python-docx (micropipでインストール)
- グラフ描画: matplotlib

【コード構造の鉄則】
- インポートエラーを避けるため、必要な外部ライブラリは冒頭で必ず await micropip.install() すること。
- JavaScriptから渡されるデータは globals().get('context') で安全に取得すること。
- 最後に必ず戻り値となる変数を置くこと。
"""

QA_SYSTEM_PROMPT = """
あなたは、ユーザーが作成したWebツールの「テクニカルアドバイザー」です。
提供されたツールのソースコード（HTML, JavaScript, Python）と仕様書を深く理解し、ユーザーの質問に的確に答えてください。

【ルール】
1. **コードの変更は行いません。** 解説やアドバイスのみを行ってください。
2. ユーザーが「CSVのフォーマットは？」「使い方は？」と聞いた場合、コード内のロジック（特にCSVパース部分や定数定義）を分析して、具体的なヘッダー列名などを回答してください。
3. 回答はMarkdown形式で見やすく整形してください。
"""

# --- 管理者認証デコレータ ---
def admin_required(f):
    """
    以下のいずれかに該当するユーザーのみアクセスを許可するデコレータ。
    1. 環境変数 ADMIN_USER_EMAIL に設定されたメールアドレス(UPN, email, preferred_username等)を持つ
    2. 環境変数 ADMIN_USER_OBJECT_ID に設定されたObject IDを持つ
    3. 環境変数 ADMIN_GROUP_OBJECT_ID に設定されたグループに所属している
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # ローカル開発用の認証スキップ
        if os.environ.get('SKIP_AUTH_FOR_LOCAL', 'false').lower() == 'true':
            return f(*args, **kwargs)

        # 1. 設定値の取得
        admin_group_ids_str = os.environ.get('ADMIN_GROUP_OBJECT_ID', '')
        admin_group_ids = [gid.strip() for gid in admin_group_ids_str.split(',') if gid.strip()]

        admin_user_ids_str = os.environ.get('ADMIN_USER_OBJECT_ID', '')
        admin_user_ids = [uid.strip() for uid in admin_user_ids_str.split(',') if uid.strip()]

        admin_user_emails_str = os.environ.get('ADMIN_USER_EMAIL', '')
        # 大文字小文字を区別しないよう小文字化
        admin_user_emails = [email.strip().lower() for email in admin_user_emails_str.split(',') if email.strip()]

        if not any([admin_group_ids, admin_user_ids, admin_user_emails]):
            logger.error("Authorization config is missing.")
            return jsonify({"success": False, "error": "サーバーの認証設定が不完全です。"}), 500

        # --- 高速チェック (ヘッダー情報のみ) ---
        
        # A. UPN (X-MS-CLIENT-PRINCIPAL-NAME) チェック
        current_upn = request.headers.get('X-MS-CLIENT-PRINCIPAL-NAME')
        if current_upn and current_upn.lower() in admin_user_emails:
            return f(*args, **kwargs)

        # B. Object ID チェック
        current_user_id = request.headers.get('X-MS-CLIENT-PRINCIPAL-ID')
        if current_user_id and current_user_id in admin_user_ids:
            return f(*args, **kwargs)

        # --- 詳細チェック (APIコール発生) ---
        # ヘッダーだけで許可されなかった場合、/.auth/me を叩いて詳細なクレーム(email等)やグループを確認する
        
        try:
            protocol = request.headers.get('X-Forwarded-Proto', 'http')
            host = request.headers.get('Host')
            auth_me_url = f"{protocol}://{host}/.auth/me"
            cookies = {key: value for key, value in request.cookies.items()}
            
            # タイムアウトを少し長めに設定
            response = requests.get(auth_me_url, cookies=cookies, timeout=10)
            response.raise_for_status()
            
            auth_data = response.json()
            if not auth_data:
                raise Exception("Empty auth data received")

            user_claims = auth_data[0].get('user_claims', [])
            
            # クレームから情報を抽出
            user_group_ids = []
            user_email_candidates = []

            for claim in user_claims:
                typ = claim.get('typ')
                val = claim.get('val')
                
                # グループID収集
                if typ == 'groups':
                    user_group_ids.append(val)
                
                # メールアドレス候補収集 (email, preferred_username, unique_name, upnなど)
                # 一般的なクレーム名をチェック
                if typ in ['http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress',
                           'http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name',
                           'preferred_username', 'email', 'mail', 'upn', 'unique_name']:
                    if val:
                        user_email_candidates.append(val.lower())

            # C. 詳細Emailチェック
            # 収集したメールアドレス候補の中に、許可リストにあるものが含まれているか
            if set(admin_user_emails) & set(user_email_candidates):
                return f(*args, **kwargs)

            # D. グループIDチェック
            if admin_group_ids and (set(admin_group_ids) & set(user_group_ids)):
                return f(*args, **kwargs)

        except Exception as e:
            # 詳細チェック中にエラーが出ても、まだ拒否とは限らないが、これ以上手立てがないのでログを出して続行
            logger.error(f"Error during deep authorization check: {e}", exc_info=True)
            pass

        # --- 最終判定: NG ---
        logger.warning(f"Unauthorized access attempt. User UPN: {current_upn} (ID: {current_user_id})")
        return jsonify({"success": False, "error": "管理者権限がありません。"}), 403
            
    return decorated_function

@sharepoint_tool_bp.route('/sharepoint-tool/run/<tool_id>')
def run_tool_page(tool_id):
    mode = request.args.get('mode', 'studio')
    return render_template('sharepoint_tool_runner.html', tool_id=tool_id, mode=mode)

@sharepoint_tool_bp.route('/sharepoint-tool/top')
def top_page():
    return render_template('sharepoint_tool_top.html')

@sharepoint_tool_bp.route('/api/sharepoint/top-data', methods=['GET'])
def api_top_data():
    """トップ画面に必要なデータを一括取得（初回ロード用）"""
    user_id = request.headers.get('X-MS-CLIENT-PRINCIPAL-ID', 'local-dev-user')
    category_filter = request.args.get('category')
    
    try:
        favorites = get_user_favorite_tools(user_id)
        my_tools = list_user_tools(user_id)
        
        # 初回はトップ20件だけ取得
        public_tools = get_public_tools(category=category_filter, limit=20, offset=0)
        
        return jsonify({
            "success": True,
            "favorites": favorites,
            "myTools": my_tools,
            "publicTools": public_tools,
            "categories": TOOL_CATEGORIES
        })
    except Exception as e:
        logger.error(f"Top data fetch failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@sharepoint_tool_bp.route('/api/sharepoint/public-tools', methods=['GET'])
def api_public_tools_page():
    category = request.args.get('category')
    limit = int(request.args.get('limit', 20))
    offset = int(request.args.get('offset', 0))
    
    try:
        tools = get_public_tools(category=category, limit=limit, offset=offset)
        return jsonify({"success": True, "tools": tools})
    except Exception as e:
        logger.error(f"Public tools fetch failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@sharepoint_tool_bp.route('/api/sharepoint/tool/<tool_id>/user-status', methods=['GET'])
def api_get_user_status(tool_id):
    user_id = request.headers.get('X-MS-CLIENT-PRINCIPAL-ID', 'local-dev-user')
    status = get_tool_interaction_status(user_id, tool_id)
    return jsonify({"success": True, "status": status})

@sharepoint_tool_bp.route('/api/sharepoint/interaction', methods=['POST'])
def api_interaction():
    user_id = request.headers.get('X-MS-CLIENT-PRINCIPAL-ID', 'local-dev-user')
    data = request.get_json()
    
    tool_id = data.get('toolId')
    target_user_id = data.get('targetUserId') # ツールの作成者ID
    interaction_type = data.get('type') # 'like' or 'favorite'
    action = data.get('action') # 'add' or 'remove'
    
    if not all([tool_id, target_user_id, interaction_type, action]):
        return jsonify({"success": False, "error": "パラメータ不足"}), 400
        
    success = toggle_interaction(user_id, tool_id, target_user_id, interaction_type, action)
    return jsonify({"success": success})

@sharepoint_tool_bp.route('/api/sharepoint/publish', methods=['POST'])
@admin_required
def api_publish():
    """公開設定変更（管理者限定）。authorIdをPartitionKeyとして使用する。"""
    data = request.get_json()
    tool_id = data.get('toolId')
    author_id = data.get('authorId')  # ツール作成者のID（PartitionKey）
    is_public = data.get('isPublic')  # boolean

    if not all([tool_id, author_id]) or is_public is None:
        return jsonify({"success": False, "error": "パラメータ不足"}), 400

    try:
        container = get_tool_cosmos_container()
        container.patch_item(
            item=tool_id,
            partition_key=author_id,
            patch_operations=[
                {"op": "set", "path": "/isPublic", "value": is_public},
                {"op": "set", "path": "/updatedAt", "value": datetime.now(timezone.utc).isoformat()}
            ]
        )
        return jsonify({"success": True})
    except cosmos_exceptions.CosmosResourceNotFoundError:
        return jsonify({"success": False, "error": "対象のツールが見つかりませんでした"}), 404
    except Exception as e:
        logger.error(f"Publish update failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@sharepoint_tool_bp.route('/api/sharepoint/check-access', methods=['POST'])
def api_check_access():
    """M365リソースへのアクセス権を事前チェックする"""
    token = request.headers.get('x-ms-token-aad-access-token')
    if not token:
        return jsonify({"success": False, "error": "トークン不足"}), 401

    data = request.get_json()
    site_id = data.get('siteId')
    if not site_id:
        return jsonify({"success": False, "error": "siteIdが必要です"}), 400

    try:
        headers = {"Authorization": f"Bearer {token}"}
        resp = requests.get(f"https://graph.microsoft.com/v1.0/sites/{site_id}", headers=headers, timeout=10)

        if resp.status_code == 200:
            return jsonify({"success": True, "hasAccess": True})
        elif resp.status_code in (401, 403):
            site_name = data.get('siteName', site_id)
            return jsonify({"success": True, "hasAccess": False, "siteName": site_name})
        else:
            return jsonify({"success": True, "hasAccess": False, "siteName": site_id})
    except Exception as e:
        logger.error(f"Access check failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@sharepoint_tool_bp.route('/api/sharepoint/discovery/resources', methods=['GET'])
def api_discover_resources():
    sid = request.args.get('siteId')
    token = request.headers.get('x-ms-token-aad-access-token')
    if not token: 
        return jsonify({"error": "トークン不足"}), 401
    
    headers = {"Authorization": f"Bearer {token}"}
    
    try:
        logger.info(f"========== START RESOURCE DISCOVERY FOR SITE: {sid} ==========")
        
        # ===== ステップ0: 親サイトの情報を取得 =====
        site_info_url = f"https://graph.microsoft.com/v1.0/sites/{sid}"
        site_info_resp = requests.get(site_info_url, headers=headers)
        site_info_resp.raise_for_status()
        site_info = site_info_resp.json()
        
        # ===== ステップ1: すべてのドライブ（ライブラリ）を取得 =====
        drives_url = f"https://graph.microsoft.com/v1.0/sites/{sid}/drives"
        all_drives = []
        
        while drives_url:
            drives_resp = requests.get(drives_url, headers=headers)
            drives_resp.raise_for_status()
            data = drives_resp.json()
            all_drives.extend(data.get('value', []))
            drives_url = data.get('@odata.nextLink')
        
        drive_ids = {d.get('id') for d in all_drives if d.get('id')}
        logger.info(f"Found {len(all_drives)} drives")
        
        # ===== ステップ2: すべてのリストを取得 =====
        lists_url = f"https://graph.microsoft.com/v1.0/sites/{sid}/lists?$expand=drive($select=id)"
        all_items = []
        
        while lists_url:
            logger.info(f"Fetching lists from: {lists_url}")
            lists_resp = requests.get(lists_url, headers=headers)
            lists_resp.raise_for_status()
            data = lists_resp.json()
            all_items.extend(data.get('value', []))
            lists_url = data.get('@odata.nextLink')
        
        logger.info(f"Total items returned from /lists API: {len(all_items)}")
        
        # ===== ステップ3: 分類ロジック =====
        pure_lists = []
        doc_libraries = []
        system_items = []
        hidden_items = []
        
        library_templates = {
            'documentLibrary', 'pictureLibrary', 'formLibrary', 
            'wikiPageLibrary', 'xmlFormLibrary', 'assetLibrary', 
            'dataConnectionLibrary', 'reportLibrary'
        }
        
        custom_list_templates = {
            'genericList', 'events', 'tasks', 'links', 'announcements', 
            'contacts', 'issueTracking', 'survey'
        }

        for item in all_items:
            display_name = item.get('displayName') or item.get('name', 'Unnamed')
            item_id = item.get('id')
            web_url = item.get('webUrl', '')
            
            is_system = item.get('system') is not None
            list_info = item.get('list', {})
            template = list_info.get('template', '')
            is_hidden = list_info.get('hidden', False)
            
            drive_info = item.get('drive')
            item_drive_id = drive_info.get('id') if isinstance(drive_info, dict) else None

            # --- 判定ロジック ---
            if is_hidden:
                hidden_items.append(display_name)
                continue

            if is_system:
                system_items.append(display_name)
                continue
            
            if template in ['catalog', 'styleLibrary', 'themeCatalog', 'designCatalog', 'ltiLibrary']:
                system_items.append(display_name)
                continue

            is_lib_template = template in library_templates
            has_drive = bool(item_drive_id)
            is_known_drive = item_drive_id in drive_ids if item_drive_id else False
            is_definitely_list = template in custom_list_templates

            if is_definitely_list:
                pass
            elif is_lib_template or is_known_drive or has_drive:
                doc_libraries.append(display_name)
                continue

            pure_lists.append({
                "id": item_id,
                "displayName": display_name,
                "template": str(template),
                "web_url": web_url
            })
        
        result = {
            "lists": pure_lists,
            "drives": all_drives,
            "subsites": [],
            "debug": {
                "total": len(all_items),
                "lists": len(pure_lists),
                "libraries": len(doc_libraries),
                "hidden": len(hidden_items),
                "system": len(system_items)
            }
        }
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@sharepoint_tool_bp.route('/api/sharepoint/generate', methods=['POST'])
def api_generate():
    data = request.get_json()
    prompt = data.get('prompt')
    attachments = data.get('attachments', [])
    single_attachment = data.get('attachment')
    if single_attachment:
        attachments.append(single_attachment)
    use_python = data.get('usePython', False)
    auto_check = data.get('autoCheck', False)
    
    # Pythonモード時の追加指示（JS側への指示）
    python_instruction = ""
    if use_python:
        python_instruction = """
        【重要: 高度処理(Python)モード有効】
        ユーザーは「高度なデータ処理（Python）」を選択しました。
        以下の役割分担ルールを **厳守** してコードを生成してください：

        ### 1. 動画・音声 (Video/Audio) の場合
        - **Pythonで処理させてはいけません**（PyodideではFFmpegが動かないため）。
        - **JavaScript側 (`ffmpeg.wasm`)** でリサイズ・変換処理を実装してください。
          - 必要なライブラリ: `<script src="https://cdn.jsdelivr.net/npm/@ffmpeg/ffmpeg@0.11.6/dist/ffmpeg.min.js"></script>` を動的にロードするか、CDNリンクがある前提で書いてください。
        - Pythonの役割: JSでの処理結果（ファイル名、圧縮率などのメタデータ）を受け取り、HTMLレポートを作成する処理のみを担当させます。
        - 呼び出し例: `await window.runPython(null, { originalSize: ..., newSize: ... });`

        ### 2. 画像 (Image) / Excel / Word / PPTX の場合
        - **Python (`Pillow`, `pandas`, `openpyxl`)** で処理させてください。
        - JS側は `window.callGraphProxy` でファイルをBase64文字列として取得し、Pythonに渡す役割に徹してください。
        - 呼び出し例: `await window.runPython(null, {{ fileBase64: fileBase64Data, fileName: fileName }});`
        - ※ Python内では必ず `base64.b64decode(context['fileBase64'])` でデコードして使用すること。

        ### 3. 共通ルール
        - 複雑なロジックはJSに書かず、可能な限りPythonへ委譲する（動画処理を除く）。
        - Pythonコード自体は出力せず、"description" に「Python側で何をするか」の仕様を記述する。
        - Pythonを呼び出す際は、必ず `window.getAuthToken()` でトークンを取得し、contextに含めること。
        """

    # ユーザープロンプトの強化
    enhanced_prompt = f"""
    {prompt}

    ---
    【UIデザインの重要指示】
    出力するHTML(UI)は、**モダンで洗練された、ユーザーが直感的に使いやすいデザイン**にしてください。
    - Tailwind CSSを活用し、適切な余白、配色、シャドウ、角丸などを用いてプロフェッショナルな外観にすること。
    - インタラクティブな要素（ボタンホバー時のエフェクト等）を含めること。
    - 結果表示エリアは見やすく整理（カード形式、ストライプテーブル等）すること。

    【追加指示: 仕様書の品質について】
    "description" フィールドの仕様書は、プロの仕様書として通用するレベルで詳細に記述してください。
    特に「概要」セクションでは、単なる機能説明ではなく、このツールがどのような業務背景で作られ、どのようなメリットをもたらすかまで想像して補完し、記述してください。
    【カテゴリ指定】
    以下のリストから最も適切な分類を選んで JSON の "category" フィールドに入れてください。
    リストにない分類は絶対に使用しないでください：
    {", ".join(TOOL_CATEGORIES)}

    {python_instruction}
    """

    # Geminiへの入力パーツ
    parts = []
    if attachments:
        for att in attachments:
            try:
                base64_data = att.get('data')
                file_name = att.get('fileName', 'unknown_file')
                if base64_data:
                    file_bytes = base64.b64decode(base64_data)
                    file_io = io.BytesIO(file_bytes)
                    file_io.filename = file_name
                    # file_processor.py の既存関数を利用（PDF等もここで判別される）
                    file_part = extract_file_content_to_part(file_io)
                    if file_part:
                        parts.append(file_part)
                        logger.info(f"Attached file processed: {file_name}")
            except Exception as e:
                logger.error(f"Attachment processing failed for {att.get('fileName')}: {e}")

    parts.append({"text": enhanced_prompt})

    try:
        response_text, _ = generate_with_gemini(
            model_name=config.DOC_GEN_MODEL_NAME,
            max_output_tokens=65535,
            contents=[{"role": "user", "parts": parts}],
            system_instruction=SHAREPOINT_GEN_SYSTEM_PROMPT,
            generation_config_override={"response_mime_type": "application/json"},
            feature_category="Tool Studio"
        )
        # 結果JSONのパース確認
        try:
            initial_tool = json.loads(response_text)
        except json.JSONDecodeError:
            # JSONパースエラー時はそのまま返す（またはエラーにする）が、ここでは再試行せずエラー情報を返す
            return jsonify({"success": False, "error": "AIが不正なJSONを生成しました"}), 500

        # 【追加】自動チェック機能
        if auto_check:
            logger.info("Auto-check requested. Performing self-correction...")
            
            check_prompt = """
            【自己検証と修正の指示】
            直前にあなたが生成したツールについて、ユーザーの元の要望（プロンプトおよび添付ファイル）と照らし合わせ、以下の点を厳しくチェックしてください。

            1. **要件の網羅性**: ユーザーが求めた機能がすべて実装されているか？漏れている機能はないか？
            2. **添付ファイルの反映**: 添付ファイル（画像やテキスト）で示されたカラム名、データ構造、デザインイメージがコードに正しく反映されているか？
            3. **UI/Logicの整合性**: UIのIDとJavaScriptのロジックで不整合はないか？
            4. **デザイン品質**: UIはユーザーから見て「モダンで使いやすい」ものになっているか？

            **問題がある場合は、修正した完全なJSONのみを出力してください。**
            **問題がない場合でも、確認済みのJSONを再度出力してください。**
            （解説や言い訳は不要です。修正後のJSONデータのみを返してください）
            """
            
            # マルチターン会話を構成して再リクエスト
            # User(要望+ファイル) -> Model(1回目の生成) -> User(チェック指示)
            check_contents = [
                {"role": "user", "parts": parts},
                {"role": "model", "parts": [{"text": response_text}]},
                {"role": "user", "parts": [{"text": check_prompt}]}
            ]
            
            try:
                response_text_checked, _ = generate_with_gemini(
                    model_name=config.DOC_GEN_MODEL_NAME,
                    max_output_tokens=65535,
                    contents=check_contents,
                    system_instruction=SHAREPOINT_GEN_SYSTEM_PROMPT,
                    generation_config_override={"response_mime_type": "application/json"},
                    feature_category="Tool Studio (AutoCheck)"
                )
                # チェック後の結果を採用
                return jsonify({"success": True, "tool": json.loads(response_text_checked)})
            except Exception as e_check:
                logger.warning(f"Auto-check failed: {e_check}. Returning initial result.")
                # チェックに失敗した場合は1回目の結果を返す
                return jsonify({"success": True, "tool": initial_tool})

        # チェックなしの場合はそのまま返す
        return jsonify({"success": True, "tool": initial_tool})

    except Exception as e:
        logger.error(f"Generate failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@sharepoint_tool_bp.route('/api/sharepoint/generate-python', methods=['POST'])
def api_generate_python():
    data = request.get_json()
    user_prompt = data.get('prompt')
    tool_description = data.get('description') # JS生成フェーズで作られた仕様書
    current_code = data.get('currentCode', '')

    reminder_prompt = ""
    keywords = (user_prompt + tool_description).lower()
    
    if "excel" in keywords or "xlsx" in keywords:
        reminder_prompt += "\n★重要: Excel処理が含まれています。必ず `await micropip.install('openpyxl')` と `await micropip.install('pandas')` を冒頭に記述してください。"
    
    if "word" in keywords or "docx" in keywords:
        reminder_prompt += "\n★重要: Word処理が含まれています。必ず `await micropip.install('python-docx')` を記述してください。"
        
    if "powerpoint" in keywords or "pptx" in keywords or "ppt" in keywords:
        reminder_prompt += "\n★重要: PowerPoint処理が含まれています。必ず `await micropip.install('python-pptx')` を記述してください。"

    if "image" in keywords or "png" in keywords or "jpg" in keywords:
        reminder_prompt += "\n★重要: 画像処理が含まれています。必ず `await micropip.install('Pillow')` を記述してください。"

    # Python生成用のプロンプト構築
    prompt = f"""
    以下のWebツールで使用する、バックエンド処理（データ加工・分析・ファイル生成）を行うPythonコードを作成してください。
    
    【ユーザーの要望】
    {user_prompt}
    
    【ツールの仕様 (JavaScript側のコンテキスト)】
    {tool_description}
    
    【実装要件】
    - Pyodide環境で動作すること。
    - 必要なパッケージ（pandas, openpyxl, python-pptx等）があれば `import micropip; await micropip.install('...')` を記述すること。
    - JavaScriptからは `context` という辞書変数経由、またはグローバル変数としてデータが渡される想定で書いてください。
    
    {reminder_prompt}

    【現在のコード (修正の場合)】
    {current_code}
    """
    
    try:
        response_text, _ = generate_with_gemini(
            model_name=config.DOC_GEN_MODEL_NAME,
            max_output_tokens=65535,
            contents=[{"role": "user", "parts": [{"text": prompt}]}],
            system_instruction=PYTHON_GEN_SYSTEM_PROMPT,
            feature_category="Tool Studio Python"
        )
        
        clean_code = ""
        
        # パターン1: ```python ... ``` で囲まれている場合 (最も一般的)
        match_python = re.search(r'```python\s*(.*?)\s*```', response_text, re.DOTALL)
        
        # パターン2: 言語指定なしの ``` ... ``` で囲まれている場合
        match_generic = re.search(r'```\s*(.*?)\s*```', response_text, re.DOTALL)

        if match_python:
            clean_code = match_python.group(1).strip()
        elif match_generic:
            clean_code = match_generic.group(1).strip()
        else:
            # コードブロックが見つからない場合、全体がコードであるとみなす（ただし説明文が含まれるリスクあり）
            # リスク軽減のため、import文が含まれているかチェックするなどの簡易フィルタを入れてもよい
            clean_code = response_text.strip()
            
            # 安全策: もし "Here is the code" のような文言から始まっていたら、除去を試みる
            # (ここでは簡易的にそのまま返すか、もしくはエラーコメントにする)
            if not clean_code.startswith("import") and not clean_code.startswith("from") and not clean_code.startswith("#"):
                 logger.warning("No code block found and content does not look like pure Python.")

        return jsonify({"success": True, "code": clean_code})
        
    except Exception as e:
        logger.error(f"Python generation failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
        
@sharepoint_tool_bp.route('/api/sharepoint/update', methods=['POST'])
def api_update():
    data = request.get_json()
    feedback = data.get('feedback')
    current_tool = data.get('currentTool')
    attachments = data.get('attachments', [])
    use_python = data.get('usePython', False)
    auto_check = data.get('autoCheck', False)
    has_python_code = bool(current_tool.get('python', '').strip())

    # FIX-1: description を 3000 文字で切り詰めてコンテキストとして渡す
    description_snippet = (current_tool.get('description') or '')[:3000]

    # FIX-4: 直近 3 件の修正履歴をテキスト化してコンテキストに追加する
    history_items = current_tool.get('history') or []
    recent_history = history_items[-3:]
    history_context = "\n".join(
        f"[{item.get('role', '?')}] {item.get('text', '')}"
        for item in recent_history
        if item.get('role') in ('user', 'ai')
    ) or "(なし)"

    # Pythonモード時の保護指示
    python_protection_prompt = ""
    if use_python or has_python_code:
        python_protection_prompt = """
        【重要: Python連携モード】
        このツールは、高度なデータ処理ロジックをPython（Pyodide）で実行する設定になっています。
        JavaScriptコード ("logic") を修正する際は、以下の点を厳守してください：

        1. 複雑なデータ加工やファイル解析（Excel/PDF等）はJavaScriptに書かず、`window.runPython` を呼び出す構造にしてください。
        2. `window.runPython(null, context)` の呼び出しを維持、または必要に応じて適切に追加してください。
        3. Python側へ渡すべきデータ（context）が不足していないか検討し、JS側で準備してください。
        """

    # FIX-1/2: toolName・description・品質維持ルールを含む強化プロンプト
    prompt = f"""
    以下のツールを修正してください。

    ツール名: {current_tool.get('toolName', '(未設定)')}
    ユーザーからの指示: {feedback}

    【ツールの仕様書（概要）】
    {description_snippet}

    【直近の変更履歴】
    {history_context}

    【現在のコード】
    UI (HTML): {current_tool.get('ui', '')}
    Logic (JavaScript): {current_tool.get('logic', '')}
    Category: {current_tool.get('category', '未分類')}

    {python_protection_prompt}

    ---
    【修正時の品質維持ルール】
    1. **指示された箇所のみを修正すること。**
    2. **仕様書との整合性**: 上記の仕様書に記述されたツールの目的・機能を維持しながら修正すること。
    3. **UI (HTML/CSS) の保護**:
       - ユーザーから「デザイン変更」「ボタン追加」等の明示的な指示がない限り、**現在のUIコード（HTML構造・クラス・スタイル）を絶対に変更しないこと。**
       - 背景色、レイアウト、フォントサイズなどが勝手に変わらないよう、元のコードを維持して出力すること。
    4. **Logic (JavaScript) の修正**:
       - ユーザーの要望を実現するために必要なロジック部分のみを修正すること。
    5. **仕様書 (description)**:
       - 修正内容に合わせて整合性が取れるよう更新すること。
    6. **toolName と category**:
       - 意味的に正しい内容を維持すること。ユーザーから変更指示がない限り変えないこと。
    """

    parts = []
    if attachments:
        for att in attachments:
            try:
                base64_data = att.get('data')
                if base64_data:
                    file_bytes = base64.b64decode(base64_data)
                    file_io = io.BytesIO(file_bytes)
                    file_io.filename = att.get('fileName', 'screenshot.png')
                    file_part = extract_file_content_to_part(file_io)
                    if file_part:
                        parts.append(file_part)
            except Exception as e:
                logger.error(f"Update attachment failed: {e}")

    parts.append({"text": prompt})

    try:
        response_text, _ = generate_with_gemini(
            model_name=config.DOC_GEN_MODEL_NAME,
            max_output_tokens=65535,
            contents=[{"role": "user", "parts": parts}],
            system_instruction=SHAREPOINT_GEN_SYSTEM_PROMPT,
            generation_config_override={"response_mime_type": "application/json"},
            feature_category="Tool Studio Update"
        )

        try:
            initial_tool = json.loads(response_text)
        except json.JSONDecodeError as e_json:
            logger.error(f"Update: AI returned invalid JSON: {e_json}")
            return jsonify({"success": False, "error": "AIが不正なJSONを生成しました"}), 500

        # FIX-3: autoCheck — 自己検証ループ（api_generate と同パターン）
        if auto_check:
            check_prompt = """
            【自己検証と修正の指示】
            直前に生成したツールについて、ユーザーの修正指示と元の仕様書を照らし合わせ、以下の点を厳しくチェックしてください。

            1. **指示の反映**: ユーザーの修正指示が完全に実装されているか？
            2. **仕様との整合性**: 元の仕様書（ツールの目的・機能一覧）が損なわれていないか？
            3. **UI/Logic の整合性**: UIのIDとJavaScriptのロジックで不整合はないか？
            4. **デザインの保全**: 指示がない箇所のUI（配色・レイアウト・クラス）が維持されているか？

            **問題がある場合は修正した完全なJSONのみを出力してください。**
            **問題がない場合でも確認済みのJSONを再度出力してください。**
            （解説や言い訳は不要です。JSONデータのみを返してください）
            """

            check_contents = [
                {"role": "user", "parts": parts},
                {"role": "model", "parts": [{"text": response_text}]},
                {"role": "user", "parts": [{"text": check_prompt}]}
            ]

            try:
                response_text_checked, _ = generate_with_gemini(
                    model_name=config.DOC_GEN_MODEL_NAME,
                    max_output_tokens=65535,
                    contents=check_contents,
                    system_instruction=SHAREPOINT_GEN_SYSTEM_PROMPT,
                    generation_config_override={"response_mime_type": "application/json"},
                    feature_category="Tool Studio Update (AutoCheck)"
                )
                return jsonify({"success": True, "tool": json.loads(response_text_checked)})
            except Exception as e_check:
                logger.warning(f"Update auto-check failed: {e_check}. Returning initial result.")
                return jsonify({"success": True, "tool": initial_tool})

        return jsonify({"success": True, "tool": initial_tool})
    except Exception as e:
        logger.error(f"Update failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@sharepoint_tool_bp.route('/api/sharepoint/tools', methods=['GET'])
def api_list_tools():
    user_id = request.headers.get('X-MS-CLIENT-PRINCIPAL-ID', 'local-dev-user')
    return jsonify({"success": True, "tools": list_user_tools(user_id)})

@sharepoint_tool_bp.route('/api/sharepoint/tool/<tool_id>', methods=['GET'])
def api_get_tool(tool_id):
    try:
        author_id = request.args.get('authorId')

        # authorId未指定の場合、まず現在のユーザーのパーティションを直接検索する。
        # これによりクロスパーティションクエリ（非決定的）を回避し、ゾンビエントリの影響を受けなくなる。
        if not author_id:
            current_user_id = request.headers.get('X-MS-CLIENT-PRINCIPAL-ID', 'local-dev-user')
            tool = get_sharepoint_tool(tool_id, current_user_id)
            if tool:
                return jsonify({"success": True, "tool": tool})

        # 上記で見つからない場合（他人のツール閲覧等）は指定authorIdまたはクロスパーティションで検索
        tool = get_sharepoint_tool(tool_id, author_id)
        if tool:
            return jsonify({"success": True, "tool": tool})
        else:
            return jsonify({"success": False, "error": "ツールが見つかりませんでした"}), 404
    except Exception as e:
        logger.error(f"Error getting tool {tool_id}: {e}", exc_info=True)
        return jsonify({"success": False, "error": "読み込み失敗"}), 500

@sharepoint_tool_bp.route('/api/sharepoint/save', methods=['POST'])
def api_save():
    user_id = request.headers.get('X-MS-CLIENT-PRINCIPAL-ID', 'local-dev-user')
    default_author = request.headers.get('X-MS-CLIENT-PRINCIPAL-NAME', 'Developer')
    data = request.get_json()
    
    # フロントから受け取ったパラメータを展開
    folder = data.get('folderName', '未分類')
    description = data.get('description', '')
    history = data.get('history', [])
    category = data.get('category', '未分類')
    is_public = data.get('isPublic', False)
    connection_settings = data.get('connectionSettings', {})

    author_name = default_author
    token = request.headers.get('x-ms-token-aad-access-token')
    
    if token:
        try:
            # 自分のプロフィールを取得 (displayName, department)
            graph_url = "https://graph.microsoft.com/v1.0/me?$select=displayName,department"
            resp = requests.get(graph_url, headers={"Authorization": f"Bearer {token}"}, timeout=5)
            
            if resp.status_code == 200:
                profile = resp.json()
                display_name = profile.get('displayName')
                department = profile.get('department')
                
                if display_name:
                    if department:
                        # "営業部・山田 太郎" の形式にする
                        author_name = f"{department}・{display_name}"
                    else:
                        # 部署がない場合は氏名のみ
                        author_name = display_name
        except Exception as e:
            # エラー時はログを出してデフォルト（メールアドレス）を使用
            logger.warning(f"Failed to fetch user profile for author name: {e}")

    tid = save_sharepoint_tool(
        user_id=user_id,
        tool_name=data['toolName'],
        ui_html=data['ui'],
        logic_js=data['logic'],
        description=description,
        history=history,
        tool_id=data.get('id'),
        folder_name=folder,
        is_public=is_public,
        category=category,
        author_name=author_name,
        python_code=data.get('python_code', ''),
        connection_settings=connection_settings
    )
    return jsonify({"success": True, "tool_id": tid})


@sharepoint_tool_bp.route('/api/sharepoint/rename/<tool_id>', methods=['POST'])
def api_rename(tool_id):
    user_id = request.headers.get('X-MS-CLIENT-PRINCIPAL-ID', 'local-dev-user')
    data = request.get_json()
    success = rename_sharepoint_tool(tool_id, user_id, data.get('toolName'))
    return jsonify({"success": success})

@sharepoint_tool_bp.route('/api/sharepoint/delete/<tool_id>', methods=['DELETE'])
def api_delete(tool_id):
    user_id = request.headers.get('X-MS-CLIENT-PRINCIPAL-ID', 'local-dev-user')
    success = delete_sharepoint_tool(tool_id, user_id)
    return jsonify({"success": success})

@sharepoint_tool_bp.route('/api/sharepoint/discovery/sites', methods=['GET'])
def api_discover_sites():
    q = request.args.get('search')
    token = request.headers.get('x-ms-token-aad-access-token')
    if not token: return jsonify({"error": "Local Dev Mode"}), 401
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(f"https://graph.microsoft.com/v1.0/sites?search={q}", headers=headers)
    return jsonify(resp.json().get('value', []))

@sharepoint_tool_bp.route('/api/sharepoint/discovery/resolve-site', methods=['GET'])
def api_resolve_site():
    site_url = request.args.get('url')
    token = request.headers.get('x-ms-token-aad-access-token')
    if not token: 
        return jsonify({"error": "認証トークンがありません (Local Dev Mode)"}), 401
    
    try:
        logger.info(f"Resolving Site URL: {site_url}")

        parsed = urlparse(site_url)
        hostname = parsed.netloc
        
        # パスがない場合（ルートサイトのみの場合）
        if not parsed.path or parsed.path == "/":
            original_path = ""
        else:
            original_path = unquote(parsed.path).strip('/')
        
        # 候補リスト作成
        candidates = []
        if original_path:
            parts = original_path.split('/')
            for i in range(len(parts), 0, -1):
                candidates.append("/".join(parts[:i]))
        candidates.append("") # 最後にルートサイト
        
        headers = {"Authorization": f"Bearer {token}"}
        
        last_error = None

        for i, path_segment in enumerate(candidates):
            # API URLの構築
            if not path_segment:
                url = f"https://graph.microsoft.com/v1.0/sites/{hostname}"
            else:
                encoded_path = quote(path_segment)
                url = f"https://graph.microsoft.com/v1.0/sites/{hostname}:/{encoded_path}"
            
            logger.info(f"Trying Candidate [{i}]: {url}")
            
            try:
                resp = requests.get(f"{url}?$select=id,displayName,webUrl,name", headers=headers)
                
                if resp.status_code == 200:
                    logger.info(f"Hit! Site Found: {path_segment}")
                    site_data = resp.json()
                    if 'displayName' not in site_data and 'name' in site_data:
                        site_data['displayName'] = site_data['name']
                    return jsonify([site_data])
                
                # ★ここが重要: 認証エラーや権限エラーの場合は即座に停止する
                # ユーザーが指定したドンピシャのパス(最初の候補)で401/403が出たなら、それは「見つからない」ではなく「入れない」なので。
                if i == 0 and resp.status_code in [401, 403]:
                    error_msg = f"Access Denied (Status: {resp.status_code}). 権限がないかトークンが無効です。"
                    logger.error(error_msg)
                    return jsonify({"error": error_msg}), resp.status_code
                
                # 404の場合は「ここにはサイトがない」ので、親階層(次の候補)を探しに行く
                logger.warning(f"Failed candidate [{path_segment}]: Status {resp.status_code} - {resp.text}")
                last_error = f"Status {resp.status_code}"

            except Exception as req_err:
                logger.error(f"Request Exception for {url}: {req_err}")
                last_error = str(req_err)
                continue
                
        # ループを抜けても返却できていない場合
        logger.error("All candidates failed.")
        return jsonify([])
        
    except Exception as e:
        logger.error(f"Site resolve critical error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@sharepoint_tool_bp.route('/api/sharepoint/proxy', methods=['POST'])
def api_proxy():
    token = request.headers.get('x-ms-token-aad-access-token')
    if not token: return jsonify({"error": "認証環境のみ"}), 401
    req_data = request.get_json()
    endpoint = req_data.get('endpoint')
    method = req_data.get('method', 'GET')
    payload = req_data.get('payload')
    custom_headers = req_data.get('headers', {})
    
    version = req_data.get('version', 'v1.0')

    if not endpoint.startswith('/'): endpoint = f"/{endpoint}"
    url = f"https://graph.microsoft.com/{version}{endpoint}"
    
    request_headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    if custom_headers: request_headers.update(custom_headers)
    
    request_kwargs = {"method": method, "url": url, "headers": request_headers}
    if payload is not None:
        # アップロード時：Base64フラグがあればバイナリに復元
        if isinstance(payload, dict) and payload.get('_is_base64'):
            request_kwargs['data'] = base64.b64decode(payload.get('content'))
        elif isinstance(payload, dict):
            request_kwargs['json'] = payload
        else:
            request_kwargs['data'] = payload

    try:
        resp = requests.request(**request_kwargs)
        if resp.status_code == 204: return jsonify({"success": True}), 204

        content_type = resp.headers.get('Content-Type', '').lower()
        # ダウンロード時：画像等のバイナリならBase64で返す
        if 'application/json' not in content_type and 'text' not in content_type:
            return jsonify({
                "is_binary": True, 
                "content": base64.b64encode(resp.content).decode('utf-8'),
                "contentType": content_type
            }), resp.status_code
        
        if 'application/json' not in content_type and 'text' in content_type:
            return jsonify({
                "is_binary": True, # JS側で content プロパティをそのまま返すように仕向ける
                "content": resp.text, # Base64ではなく生テキスト
                "contentType": content_type,
                "is_text": True # 識別用フラグ
            }), resp.status_code


        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        logger.error(f"Proxy Error: {e}")
        return jsonify({"error": str(e)}), 500


@sharepoint_tool_bp.route('/api/sharepoint/create-empty-excel', methods=['POST'])
def api_create_empty_excel():
    """
    openpyxl で有効な空の xlsx を生成し、OneDrive または SharePoint の指定パスにアップロードする。
    AI が Base64 を捏造して FileCorruptTryRepair になる問題を回避するための専用エンドポイント。

    Body params:
        folder   : 格納先フォルダ名 (例: "Documents", "見積書")
        filename : ファイル名 (例: "book.xlsx")
        driveId  : (省略可) "me" → OneDrive個人, それ以外 → SharePoint ドライブID
    """
    token = request.headers.get('x-ms-token-aad-access-token')
    if not token:
        return jsonify({"error": "認証環境のみ"}), 401

    data = request.get_json()
    folder   = (data.get('folder')   or 'Documents').strip('/')
    filename = (data.get('filename') or 'book.xlsx').strip('/')
    drive_id = (data.get('driveId')  or 'me').strip()

    # openpyxl で最小構成の有効な xlsx をメモリ上で生成
    try:
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Sheet1"
        buf = io.BytesIO()
        wb.save(buf)
        xlsx_bytes = buf.getvalue()
    except Exception as e:
        logger.error(f"openpyxl workbook creation failed: {e}")
        return jsonify({"error": f"ファイル生成エラー: {e}"}), 500

    # driveId に応じてアップロード先 URL を切り替え
    # OneDrive (個人): /me/drive/root:/{folder}/{filename}:/content
    # SharePoint      : /drives/{driveId}/root:/{folder}/{filename}:/content
    if drive_id == 'me':
        upload_url = f"https://graph.microsoft.com/v1.0/me/drive/root:/{folder}/{filename}:/content"
    else:
        upload_url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/{folder}/{filename}:/content"

    req_headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    }
    try:
        resp = requests.put(upload_url, headers=req_headers, data=xlsx_bytes)
        resp.raise_for_status()
        item = resp.json()
        return jsonify({"id": item.get("id"), "name": item.get("name")}), 200
    except Exception as e:
        logger.error(f"Excel upload failed (driveId={drive_id}): {e}")
        return jsonify({"error": f"アップロードエラー: {e}"}), 500


@sharepoint_tool_bp.route('/api/sharepoint/summarize', methods=['POST'])
def api_summarize():
    data = request.get_json()
    ui = data.get('ui')
    logic = data.get('logic')
    
    prompt = f"""
    以下のコードを解析し、プロフェッショナルな『ツール詳細仕様書』をMarkdownで作成してください。
    
    【要件】
    1. 挨拶や話し言葉は禁止。
    2. 「概要」セクションでは、コードから読み取れる意図を汲み取り、業務課題と解決策という文脈で詳細に記述すること。
    3. 入力・出力項目、内部ロジック、エラーチェック仕様を網羅すること。
    4. 可能な限り表形式（Table）を用いて構造化すること。

    【UI】
    {ui}

    【Logic】
    {logic}
    """

    try:
        response_text, _ = generate_with_gemini(
            model_name=config.DOC_GEN_MODEL_NAME,
            max_output_tokens=65535,
            contents=[{"role": "user", "parts": [{"text": prompt}]}],
            system_instruction="あなたはシステムのテクニカルライターです。専門用語を適切に使い、論理的かつ詳細なドキュメントを作成してください。",
            feature_category="Tool Studio"
        )
        return jsonify({"success": True, "description": response_text})
    except Exception as e:
        logger.error(f"Summarize failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
    
@sharepoint_tool_bp.route('/api/sharepoint/tool-ai-execute', methods=['POST'])
def api_tool_ai_execute():
    token = request.headers.get('x-ms-token-aad-access-token')
    user_id = request.headers.get('X-MS-CLIENT-PRINCIPAL-ID', 'local-dev-user')
    data = request.get_json()
    
    tool_id = data.get('toolId')
    prompt = data.get('prompt')
    item_id = data.get('itemId')
    drive_id = data.get('driveId')
    system_instruction = data.get('systemInstruction')
    llm_model = data.get('llmModel', 'standard')

    if not token: return jsonify({"success": False, "error": "認証トークンがありません。"}), 401

    # ツールのconnectionSettingsからuseAIを確認
    tool_data = get_sharepoint_tool(tool_id)
    if tool_data:
        conn_settings = tool_data.get('connectionSettings', {})
        if conn_settings.get('useAI') is False:
            return jsonify({"success": False, "error": "このツールではAI生成機能が無効に設定されています。"}), 403
        # connectionSettingsのllmModelを優先（フロントからの値はフォールバック）
        llm_model = conn_settings.get('llmModel', llm_model)

    file_part = None
    try:
        if item_id and drive_id:
            meta_url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{item_id}"
            meta_resp = requests.get(meta_url, headers={"Authorization": f"Bearer {token}"})
            meta_resp.raise_for_status()
            filename = meta_resp.json().get('name', 'unknown.txt')
            content_url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{item_id}/content"
            content_resp = requests.get(content_url, headers={"Authorization": f"Bearer {token}"})
            content_resp.raise_for_status()
            file_io = io.BytesIO(content_resp.content)
            file_io.filename = filename
            file_part = extract_file_content_to_part(file_io)
    except Exception as e:
        logger.error(f"File Fetch/Process Error: {e}")
        return jsonify({"success": False, "error": f"ファイル読込失敗: {str(e)}"}), 500

    # モデル選択: advanced -> DOC_GEN_MODEL_NAME, standard -> DEFAULT_MODEL_NAME
    selected_model = config.DOC_GEN_MODEL_NAME if llm_model == 'advanced' else config.DEFAULT_MODEL_NAME
    model_type = 'advanced' if llm_model == 'advanced' else 'standard'

    try:
        contents_list = []
        if file_part: contents_list.append({"role": "user", "parts": [file_part, {"text": prompt}]})
        else: contents_list.append({"role": "user", "parts": [{"text": prompt}]})

        # Feature Category に Tool ID を含めてログで識別可能にする
        log_category = f"Tool Studio: {tool_id}" if tool_id else "Tool Studio: Unknown"

        response_text, metadata = generate_with_gemini(
            model_name=selected_model,
            max_output_tokens=65535,
            contents=contents_list,
            user_info={"id": user_id},
            system_instruction=system_instruction,
            feature_category=log_category
        )
        # Token usage check
        total_tokens = 0
        if hasattr(metadata, 'total_token_count'): total_tokens = metadata.total_token_count
        elif isinstance(metadata, dict): total_tokens = metadata.get("usage", {}).get("total_tokens", 0)

        if not check_and_update_token_usage(tool_id, total_tokens, model_type=model_type):
            return jsonify({"success": False, "error": "本日のAI利用上限に達しました。"}), 429
        return jsonify({"success": True, "answer": response_text})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@sharepoint_tool_bp.route('/api/sharepoint/discovery/folders', methods=['GET'])
def api_discover_folders():
    resource = request.args.get('resource')
    parent_id = request.args.get('parentId')  # サブフォルダ取得用: 親フォルダのアイテムID
    token = request.headers.get('x-ms-token-aad-access-token')

    if not token:
        return jsonify({"error": "トークン不足"}), 401
    if not resource or ':' not in resource:
        return jsonify([])

    # parentId はアイテムID形式（英数字・ハイフン・アンダースコア・"!"・"."）のみ許可
    if parent_id and not re.fullmatch(r'[A-Za-z0-9\-_!.]{1,200}', parent_id):
        return jsonify({"error": "不正な parentId です"}), 400

    res_type, res_id = resource.split(':', 1)
    headers = {"Authorization": f"Bearer {token}"}
    folders = []

    try:
        if res_type == 'drive':
            if parent_id:
                url = f"https://graph.microsoft.com/v1.0/drives/{res_id}/items/{parent_id}/children?$select=id,name,folder"
            else:
                url = f"https://graph.microsoft.com/v1.0/drives/{res_id}/root/children?$select=id,name,folder"
            resp = requests.get(url, headers=headers)
            resp.raise_for_status()
            items = resp.json().get('value', [])

            for item in items:
                if 'folder' in item:
                    folders.append({
                        "id": item['id'],
                        "name": item['name'],
                        "childCount": item['folder'].get('childCount', 0)
                    })
        elif res_type == 'onedrive':
            if parent_id:
                url = f"https://graph.microsoft.com/v1.0/me/drive/items/{parent_id}/children?$select=id,name,folder"
            else:
                url = "https://graph.microsoft.com/v1.0/me/drive/root/children?$select=id,name,folder"
            resp = requests.get(url, headers=headers)
            if not resp.ok:
                # Graph API のエラー詳細をログに記録してデバッグを容易にする
                logger.error(f"OneDrive folder discovery: Graph API returned {resp.status_code}")
                logger.error(f"  URL: {url}")
                logger.error(f"  Response: {resp.text[:500]}")
                resp.raise_for_status()
            items = resp.json().get('value', [])
            for item in items:
                if 'folder' in item:
                    folders.append({
                        "id": item['id'],
                        "name": item['name'],
                        "childCount": item['folder'].get('childCount', 0)
                    })
        elif res_type == 'list':
            pass

        return jsonify(folders)

    except Exception as e:
        logger.error(f"Folder discovery failed: {type(e).__name__}: {e}")
        return jsonify({"error": str(e)}), 500

@sharepoint_tool_bp.route('/api/discovery/outlook/folders', methods=['GET'])
def api_discover_outlook_folders():
    """Outlookの全メールフォルダを再帰的に取得し、階層構造をパスとして返す"""
    token = request.headers.get('x-ms-token-aad-access-token')
    if not token: return jsonify({"error": "Token missing"}), 401
    
    headers = {"Authorization": f"Bearer {token}"}
    result_folders = []
    
    # 再帰探索用の内部関数
    def fetch_recursive(url, parent_path=""):
        # 安全装置: フォルダ数が多すぎる場合はサーバー負荷防止のため打ち切る (例: 500件)
        if len(result_folders) > 500:
            return

        try:
            resp = requests.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            
            items = data.get('value', [])
            for item in items:
                current_name = item['displayName']
                # パス表記を作成 (例: 受信トレイ > 案件A > 2024)
                full_display_name = f"{parent_path} > {current_name}" if parent_path else current_name
                
                result_folders.append({
                    "id": item['id'],
                    "displayName": full_display_name,
                    "unreadItemCount": item.get('unreadItemCount', 0)
                })
                
                # 子フォルダが存在する場合、さらに深く潜る (再帰呼び出し)
                if item.get('childFolderCount', 0) > 0:
                    child_url = f"https://graph.microsoft.com/v1.0/me/mailFolders/{item['id']}/childFolders?$top=100"
                    fetch_recursive(child_url, full_display_name)

            # 同階層のページング (フォルダが1階層に100個以上ある場合)
            next_link = data.get('@odata.nextLink')
            if next_link:
                fetch_recursive(next_link, parent_path)

        except Exception as e:
            logger.warning(f"Folder fetch error at {url}: {e}")
            # 一部のフォルダで失敗しても、取得できた分だけは返すためにエラーは握りつぶして続行
            pass

    try:
        # 初回: ルートフォルダから探索開始
        fetch_recursive("https://graph.microsoft.com/v1.0/me/mailFolders?$top=100")
        
        # 画面で見やすいように名前順でソートして返す
        result_folders.sort(key=lambda x: x['displayName'])
        
        return jsonify(result_folders)

    except Exception as e:
        logger.error(f"Outlook discovery failed: {e}")
        return jsonify({"error": str(e)}), 500

@sharepoint_tool_bp.route('/api/discovery/planner/plans', methods=['GET'])
def api_discover_planner_plans():
    """ユーザーがアクセス可能なPlannerのプラン一覧を取得"""
    token = request.headers.get('x-ms-token-aad-access-token')
    if not token: return jsonify({"error": "Token missing"}), 401
    
    headers = {"Authorization": f"Bearer {token}"}
    try:
        url = "https://graph.microsoft.com/v1.0/me/planner/plans"
        resp = requests.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        
        plans = [{
            "id": item['id'],
            "title": item['title'],
            "owner": item.get('owner', 'unknown')
        } for item in data.get('value', [])]
        
        return jsonify(plans)
    except Exception as e:
        logger.error(f"Planner discovery failed: {e}")
        return jsonify({"error": str(e)}), 500

@sharepoint_tool_bp.route('/api/discovery/teams', methods=['GET'])
def api_discover_teams():
    """参加しているチーム一覧を取得"""
    token = request.headers.get('x-ms-token-aad-access-token')
    if not token: return jsonify({"error": "Token missing"}), 401
    
    headers = {"Authorization": f"Bearer {token}"}
    try:
        url = "https://graph.microsoft.com/v1.0/me/joinedTeams"
        resp = requests.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        
        teams = [{
            "id": item['id'],
            "displayName": item['displayName'],
            "description": item.get('description', '')
        } for item in data.get('value', [])]
        
        return jsonify(teams)
    except Exception as e:
        logger.error(f"Teams discovery failed: {e}")
        return jsonify({"error": str(e)}), 500

@sharepoint_tool_bp.route('/api/discovery/teams/<team_id>/channels', methods=['GET'])
def api_discover_team_channels(team_id):
    """指定チーム内のチャネル一覧を取得"""
    token = request.headers.get('x-ms-token-aad-access-token')
    if not token: return jsonify({"error": "Token missing"}), 401
    
    headers = {"Authorization": f"Bearer {token}"}
    try:
        url = f"https://graph.microsoft.com/v1.0/teams/{team_id}/channels"
        resp = requests.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        
        channels = [{
            "id": item['id'],
            "displayName": item['displayName'],
            "description": item.get('description', '')
        } for item in data.get('value', [])]
        
        return jsonify(channels)
    except Exception as e:
        logger.error(f"Channels discovery failed: {e}")
        return jsonify({"error": str(e)}), 500
    
@sharepoint_tool_bp.route('/api/sharepoint/me/token', methods=['GET'])
def api_get_my_token():
    """
    クライアントサイド(Pyodide)からGraph APIを直接叩くためのトークンを提供。
    App Service認証(Easy Auth)環境を想定。
    """
    token = request.headers.get('x-ms-token-aad-access-token')
    
    # ローカル開発などでヘッダーがない場合のフォールバック（必要に応じて実装）
    if not token:
        # 開発環境用ダミートークンなどを返すか、404を返す
        return jsonify({"token": None, "error": "No token found"}), 404
        
    return jsonify({"token": token})

@sharepoint_tool_bp.route('/api/sharepoint/tool/<tool_id>/view', methods=['POST'])
def api_record_view(tool_id):
    # 閲覧にはツールの所有者(authorId)の情報が必要です（PartitionKeyのため）
    data = request.get_json()
    author_id = data.get('authorId')
    
    if not author_id:
        return jsonify({"success": False, "error": "Author ID required"}), 400

    try:
        new_count = increment_tool_view_count(tool_id, author_id)
        return jsonify({"success": True, "viewCount": new_count})
    except Exception as e:
        logger.error(f"View count error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
    
@sharepoint_tool_bp.route('/api/sharepoint/qa', methods=['POST'])
def api_tool_qa():
    """ツールに関する質問に答えるエンドポイント"""
    data = request.get_json()
    question = data.get('question')
    tool_context = data.get('toolContext') # {ui, logic, python, description}

    if not question or not tool_context:
        return jsonify({"success": False, "error": "情報が不足しています"}), 400

    # コンテキスト情報の構築
    context_text = f"""
    【対象ツールの情報】
    Name: {tool_context.get('toolName')}
    Category: {tool_context.get('category')}
    
    --- DESCRIPTION (仕様書) ---
    {tool_context.get('description')}

    --- UI (HTML) ---
    {tool_context.get('ui')}

    --- LOGIC (JavaScript) ---
    {tool_context.get('logic')}
    
    --- PYTHON CODE ---
    {tool_context.get('python')}
    """

    prompt = f"""
    ユーザーからの質問:
    {question}

    上記のツールコードに基づき、具体的に回答してください。
    """

    try:
        response_text, _ = generate_with_gemini(
            model_name=config.DEFAULT_MODEL_NAME, # 高速なモデルでOK
            max_output_tokens=8192,
            contents=[{"role": "user", "parts": [{"text": context_text}, {"text": prompt}]}],
            system_instruction=QA_SYSTEM_PROMPT,
            feature_category="Tool Studio QA"
        )
        return jsonify({"success": True, "answer": response_text})

    except Exception as e:
        logger.error(f"QA failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@sharepoint_tool_bp.route('/api/discovery/meetings', methods=['GET'])
def api_discover_meetings():
    """直近のオンライン会議一覧を取得"""
    token = request.headers.get('x-ms-token-aad-access-token')
    if not token: return jsonify({"error": "Token missing"}), 401
    
    headers = {"Authorization": f"Bearer {token}"}
    try:
        # 直近30日～未来の会議を取得
        # isOnlineMeeting eq true でフィルタリング
        today = datetime.now(timezone.utc).isoformat()
        
        # カレンダーから取得 (主催でなくても参加予定なら取れる)
        # $select で必要な項目を絞る
        url = "https://graph.microsoft.com/v1.0/me/calendar/events?$filter=isOnlineMeeting eq true&$orderby=start/dateTime desc&$top=20&$select=id,subject,start,end,onlineMeeting,isOnlineMeeting"
        
        resp = requests.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        
        meetings = []
        for item in data.get('value', []):
            join_url = None
            if item.get('onlineMeeting') and item['onlineMeeting'].get('joinUrl'):
                join_url = item['onlineMeeting']['joinUrl']
            
            if join_url:
                meetings.append({
                    "id": item['id'], # これはEventIDでありMeetingIDではない点に注意
                    "subject": item['subject'],
                    "start": item['start'],
                    "joinUrl": join_url
                })
        
        return jsonify(meetings)
    except Exception as e:
        logger.error(f"Meeting discovery failed: {e}")
        return jsonify({"error": str(e)}), 500

@sharepoint_tool_bp.route('/api/discovery/todo/lists', methods=['GET'])
def api_discover_todo_lists():
    """ユーザーのTo Doタスクリスト一覧を取得"""
    token = request.headers.get('x-ms-token-aad-access-token')
    if not token: return jsonify({"error": "Token missing"}), 401
    
    headers = {"Authorization": f"Bearer {token}"}
    try:
        url = "https://graph.microsoft.com/v1.0/me/todo/lists"
        resp = requests.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        
        lists = [{
            "id": item['id'],
            "displayName": item['displayName'],
            "isDefault": item.get('isDefault', False)
        } for item in data.get('value', [])]
        
        return jsonify(lists)
    except Exception as e:
        logger.error(f"To Do discovery failed: {e}")
        return jsonify({"error": str(e)}), 500
    
@sharepoint_tool_bp.route('/api/sharepoint/discovery/columns', methods=['GET'])
def api_discover_columns():
    """指定されたサイト・リストのカラム定義を取得する"""
    site_id = request.args.get('siteId')
    list_id = request.args.get('listId')
    token = request.headers.get('x-ms-token-aad-access-token')
    
    if not token or not site_id or not list_id:
        return jsonify({"error": "パラメータまたはトークンが不足しています"}), 400
    
    headers = {"Authorization": f"Bearer {token}"}
    
    try:
        # システムカラムを含めて取得
        url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/lists/{list_id}/columns"
        resp = requests.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        
        columns = []
        # AIにとってノイズとなる不要なシステムカラムを除外するためのリスト
        excluded_columns = [
            'Edit', 'LinkTitleNoMenu', 'LinkTitle', 'AppAuthor', 'AppEditor', 
            'FolderChildCount', 'ItemChildCount', 'ComplianceAssetId'
        ]

        for col in data.get('value', []):
            name = col.get('name') # Internal Name (API利用時に必須)
            display_name = col.get('displayName') # UI表示用
            
            if name == 'Attachments':
                # 添付ファイルは hidden=True で返ってくることが多いが、除外してはいけない
                columns.append({
                    "internalName": "Attachments",
                    "displayName": display_name or "Attachments",
                    "type": "attachments", # ★型を 'boolean' ではなく 'attachments' と明示
                    "readOnly": True,
                    "description": "List item attachments (Requires $expand=attachments)"
                })
                continue

            # 読み取り専用や隠しカラムの判定
            is_read_only = col.get('readOnly', False)
            is_hidden = col.get('hidden', False)

            # 必須システムカラム(ID, Title, Modified, Created等)は残し、それ以外の隠し/システム列を除外
            if name in excluded_columns:
                continue
            # hiddenでもIDやTitle等は重要なのでキープするロジックが必要だが、
            # Graph APIでは主要カラムはhidden=falseで返ることが多い。
            # ここではシンプルに「隠しフィールドかつ主要フィールドでない」ものを除外
            if is_hidden and name not in ['ID', 'Title', 'Author', 'Editor', 'Created', 'Modified']:
                continue

            # データ型の判定
            col_type = "text"
            if 'number' in col: col_type = "number"
            elif 'dateTime' in col: col_type = "dateTime"
            elif 'choice' in col: col_type = "choice"
            elif 'boolean' in col: col_type = "boolean"
            elif 'lookup' in col: col_type = "lookup"
            elif 'personOrGroup' in col: col_type = "person"
            elif 'calculated' in col: col_type = "calculated"
            
            columns.append({
                "internalName": name,
                "displayName": display_name,
                "type": col_type,
                "readOnly": is_read_only,
                "description": col.get('description', '')
            })
            
        return jsonify({"success": True, "columns": columns})
        
    except Exception as e:
        logger.error(f"Column discovery failed: {e}")
        return jsonify({"error": str(e)}), 500
    
@sharepoint_tool_bp.route('/sharepoint-tool/admin')
@admin_required
def admin_page():
    """管理画面ページを表示します。"""
    return render_template('sharepoint_tool_admin.html')

@sharepoint_tool_bp.route('/api/sharepoint/admin/tools', methods=['GET'])
@admin_required
def api_admin_list_tools():
    """管理画面用のツール一覧を、検索・ページネーション・ソート付きで取得します。"""
    try:
        search_query = request.args.get('search', None)
        author_name = request.args.get('author', None)
        limit = int(request.args.get('limit', 50))
        offset = int(request.args.get('offset', 0))
        date_from = request.args.get('from', None)
        date_to = request.args.get('to', None)
        sort_by = request.args.get('sort_by', 'updatedAt')
        sort_order = request.args.get('sort_order', 'DESC')

        result = list_all_tools_for_admin(
            search_query=search_query,
            author_name=author_name,
            date_from=date_from,
            date_to=date_to,
            sort_by=sort_by,
            sort_order=sort_order,
            limit=limit,
            offset=offset
        )
        return jsonify({"success": True, **result})
    except Exception as e:
        logger.error(f"Admin tool list fetch failed: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500

@sharepoint_tool_bp.route('/api/sharepoint/admin/publish', methods=['POST'])
@admin_required
def api_admin_publish():
    """管理者が任意のツールの公開状態を更新します。"""
    # ここでも管理者認証を行うのが望ましいです。
    data = request.get_json()
    tool_id = data.get('toolId')
    author_id = data.get('authorId')  # PartitionKeyとしてツールの所有者IDが必須
    is_public = data.get('isPublic')

    if not all([tool_id, author_id]) or is_public is None:
        return jsonify({"success": False, "error": "パラメータが不足しています"}), 400

    try:
        container = get_tool_cosmos_container()
        container.patch_item(
            item=tool_id,
            partition_key=author_id,
            patch_operations=[
                {"op": "set", "path": "/isPublic", "value": is_public},
                {"op": "set", "path": "/updatedAt", "value": datetime.now(timezone.utc).isoformat()}
            ]
        )
        return jsonify({"success": True})
    except cosmos_exceptions.CosmosResourceNotFoundError:
        return jsonify({"success": False, "error": "対象のツールが見つかりませんでした"}), 404
    except Exception as e:
        logger.error(f"Admin publish update failed: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500

@sharepoint_tool_bp.route('/api/sharepoint/admin/tools/<tool_id>', methods=['DELETE'])
@admin_required
def api_admin_delete_tool(tool_id):
    """管理者が任意のツールを削除します。"""
    data = request.get_json()
    author_id = data.get('authorId') if data else None

    if not author_id:
        return jsonify({"success": False, "error": "パラメータが不足しています"}), 400

    success = delete_sharepoint_tool(tool_id, author_id)
    if success:
        return jsonify({"success": True})
    else:
        return jsonify({"success": False, "error": "削除に失敗しました"}), 500
