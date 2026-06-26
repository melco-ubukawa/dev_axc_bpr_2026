import io
import logging
import traceback
import base64
import uuid
import os
import sys
from contextlib import redirect_stdout

# --- データ分析・可視化ライブラリ ---
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import seaborn as sns
import japanize_matplotlib
import pandas as pd
import numpy as np
import statsmodels.api as sm
from scipy import stats
from scipy.stats import pearsonr, spearmanr, kendalltau, ttest_ind, chi2_contingency, f_oneway
from statsmodels.iolib.summary import Summary
from statsmodels.graphics.tsaplots import plot_acf, plot_pacf
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler, OneHotEncoder, LabelEncoder
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LinearRegression, LogisticRegression
from sklearn.ensemble import RandomForestRegressor, RandomForestClassifier
from sklearn.cluster import KMeans
from sklearn.metrics import (
    accuracy_score, confusion_matrix, classification_report,
    mean_squared_error, r2_score, mean_absolute_percentage_error
)
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline

from .storage import _upload_to_gcs
import config

# --- ロガー設定 ---
logger = logging.getLogger(__name__)

# --- オプショナルなライブラリの読み込み ---
try:
    import lightgbm as lgb
except ImportError:
    lgb = None
    logger.warning("LightGBM library is not installed and will not be available in the sandbox.")

try:
    import xgboost as xgb
except ImportError:
    xgb = None
    logger.warning("XGBoost library is not installed and will not be available in the sandbox.")

try:
    import catboost as cb
except ImportError:
    cb = None
    logger.warning("CatBoost library is not installed and will not be available in the sandbox.")

try:
    from prophet import Prophet
except ImportError:
    Prophet = None
    logger.warning("Prophet library is not installed and will not be available in the sandbox.")

# --- グローバル変数 ---
execution_contexts = {}

def get_updated_python_execution_instruction() -> str:
    """
    AIにPythonコードまたは自然言語での分析応答を生成させるための、強化されたシステム指示を返す。
    """
    return """
## 【あなたの役割】
あなたは、データ分析タスクを支援する高度なAIアシスタントです。ユーザーの指示を分析し、**以下のいずれかのアクションを1つだけ選択して応答してください。**

1.  **Pythonコードの生成:**
    *   **もしユーザーの指示が**、データ操作、計算、グラフ作成、統計分析など、**コードで実行すべき具体的なタスクである場合**、あなたは**` ```python ... ``` `で囲まれたPythonコードブロックのみ**を生成しなければなりません。
    *   コード以外の挨拶、説明、言い訳、謝罪、前置き、後書きは**一切含めないでください。**

2.  **自然言語での応答:**
    *   **もしユーザーの指示が**、データの概要説明、分析結果の考察、次のステップの相談など、**自然言語で応答すべき内容である場合**、あなたは**コードブロックを一切含めずに**、テキストで応答してください。
    *   この場合、あなたは通常の対話型AIとして振る舞います。

### 【最重要ルール】データフレーム `df` の扱いについて
- **前提条件:** ユーザーがファイルをアップロードした場合、システムが既に `df` という名前のpandas DataFrameにデータを読み込んでいます。この事実は会話履歴にも `df.head()` の実行結果として提示されています。
- **【絶対禁止事項】データ読み込み・定義の全面禁止:**
  - `pd.read_csv()`, `pd.read_excel()`, `io.StringIO()` など、データを読み込んだり `df` 変数を再定義したりするコードは**絶対に生成しないでください。**
- **あなたの役割:** あなたの役割は、**常に利用可能な `df` という変数を使うことだけ**です。

### 3. グラフ描画とその他のルール
- `matplotlib` や `seaborn` を使う際は、コードブロックの**先頭**で**必ず `japanize_matplotlib.japanize()` を呼び出してください。**
- **【絶対禁止】絶対に、`plt.show()` や `plt.close()` をコードに含めないでください。**
- `exit()` や `quit()` をコードに含めないでください。
- 利用可能なライブラリ: `pandas as pd`, `numpy as np`, `matplotlib.pyplot as plt`, `seaborn as sns`, `japanize_matplotlib`, `statsmodels.api as sm`, `scipy.stats as stats`, `scikit-learn`の主要関数, `lightgbm as lgb`, `xgboost as xgb`, `catboost as cb`, `Prophet`。
"""

def execute_python_safely(code_block: str, session_id: str, global_vars: dict = None) -> dict:
    """
    Pythonコードブロックを安全なスコープで実行し、結果またはエラーを構造化して返す。
    グラフはGCSにアップロードし、そのURIを結果に含める。
    """
    logger.info(f"Executing Python code in sandbox for session/project: {session_id}")

    if session_id not in execution_contexts:
        execution_contexts[session_id] = {
            "pd": pd, "np": np, "plt": plt, "sns": sns, "japanize_matplotlib": japanize_matplotlib,
            "sm": sm, "stats": stats, "plot_acf": plot_acf, "plot_pacf": plot_pacf,
            # ★★★ 修正箇所: 統計関数をコンテキストに追加 ★★★
            "pearsonr": pearsonr, "spearmanr": spearmanr, "kendalltau": kendalltau,
            "ttest_ind": ttest_ind, "chi2_contingency": chi2_contingency, "f_oneway": f_oneway,
            
            "train_test_split": train_test_split, "StandardScaler": StandardScaler,
            "OneHotEncoder": OneHotEncoder, "LabelEncoder": LabelEncoder,
            "SimpleImputer": SimpleImputer,
            "ColumnTransformer": ColumnTransformer, "Pipeline": Pipeline,
            "LinearRegression": LinearRegression, "LogisticRegression": LogisticRegression,
            "RandomForestRegressor": RandomForestRegressor, "RandomForestClassifier": RandomForestClassifier,
            "KMeans": KMeans, "accuracy_score": accuracy_score, "confusion_matrix": confusion_matrix,
            "classification_report": classification_report, "mean_squared_error": mean_squared_error,
            "r2_score": r2_score, "mean_absolute_percentage_error": mean_absolute_percentage_error
        }
        if lgb: execution_contexts[session_id]["lgb"] = lgb
        if xgb: execution_contexts[session_id]["xgb"] = xgb
        if cb: execution_contexts[session_id]["cb"] = cb
        if Prophet: execution_contexts[session_id]["Prophet"] = Prophet
        logger.info(f"Initialized new execution context for: {session_id}")

    exec_scope = execution_contexts[session_id].copy()
    if global_vars:
        exec_scope.update(global_vars)

    current_run_results = {"results": [], "error": None, "final_scope": {}}
    
    plt.close('all')
    figures_before_exec = set(plt.get_fignums())
    
    try:
        japanize_matplotlib.japanize()
    except Exception as e:
        logger.warning(f"japanize_matplotlib.japanize() の呼び出しに失敗しました: {e}")

    def custom_plt_show_and_capture(*args, **kwargs):
        pass
    
    original_plt_show = plt.show
    exec_scope.setdefault('plt', plt).show = custom_plt_show_and_capture
            
    stdout_buffer = io.StringIO()
    
    try:
        with redirect_stdout(stdout_buffer):
            exec(code_block, exec_scope)
        
        stdout_content = stdout_buffer.getvalue()
        if stdout_content:
            current_run_results["results"].append({"type": "stdout", "content": stdout_content})

    except Exception as e:
        error_traceback = f"コード実行エラー: {type(e).__name__}\n{traceback.format_exc()}"
        logger.error(f"Sandbox execution failed for {session_id}: {error_traceback}")
        current_run_results["error"] = error_traceback
        stdout_content = stdout_buffer.getvalue()
        if stdout_content:
            current_run_results["results"].append({"type": "stdout", "content": stdout_content})
    finally:
        if 'plt' in exec_scope:
            exec_scope['plt'].show = original_plt_show

    figures_after_exec = set(plt.get_fignums())
    new_figure_numbers = figures_after_exec - figures_before_exec

    if new_figure_numbers:
        gcs_bucket_name = os.environ.get("GOOGLE_CLOUD_STORAGE_BUCKET_NAME")
        if not gcs_bucket_name:
            current_run_results["results"].append({"type": "execution_error", "content": "サーバーエラー: グラフ保存用のGCSバケットが設定されていません。"})
        else:
            for i in new_figure_numbers:
                fig = plt.figure(i)
                try:
                    buf = io.BytesIO()
                    japanize_matplotlib.japanize() 
                    fig.canvas.draw()
                    fig.savefig(buf, format='png', bbox_inches='tight', dpi=100)
                    plt.close(fig)
                    buf.seek(0)
                    
                    unique_filename = f"{uuid.uuid4().hex}.png"
                    destination_blob_name = f"{config.GCS_CHAT_ARTIFACT_FOLDER}{unique_filename}"
                    gcs_uri = _upload_to_gcs(buf, destination_blob_name, gcs_bucket_name)

                    if gcs_uri:
                        current_run_results["results"].append({"type": "chart_gcs_uri", "content": gcs_uri})
                except Exception as ex:
                    logger.error(f"Error processing matplotlib figure {i}: {ex}", exc_info=True)
                    plt.close(fig)

    processed_objects = set()
    for key, value in list(exec_scope.items()):
        if key.startswith("__") or key in execution_contexts[session_id]:
            continue
        
        # 実行後のスコープにある変数を final_scope に保存
        current_run_results["final_scope"][key] = value

        if id(value) in processed_objects:
            continue
        
        if isinstance(value, pd.DataFrame):
            df_content = {
                'name': key,
                'html': value.head().to_html(classes="dataframe-table", index=False, border=0)
            }
            current_run_results["results"].append({'type': 'dataframe', 'content': df_content})
            processed_objects.add(id(value))
        
        elif hasattr(value, 'summary') and callable(value.summary) and isinstance(value.summary(), Summary):
            stat_model_content = {'name': key, 'html': value.summary().as_html()}
            current_run_results["results"].append({'type': 'stat_model', 'content': stat_model_content})
            processed_objects.add(id(value))

    plt.close('all')
    return current_run_results