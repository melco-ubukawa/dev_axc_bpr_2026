import os
import re
import json
import logging
import subprocess
import tempfile
import base64
import html
from .gemini import generate_with_gemini
from google.genai import types
import config

logger = logging.getLogger(__name__)

class DiagramGenerator:
    """
    テキスト指示から図解コードを生成し、レンダリングするクラス。
    Mermaidは使用せず、PlantUML, Graphviz(DOT), SVG(Raw), HTMLをサポート。
    """
    def __init__(self, user_info=None):
        self.user_info = user_info

    def generate_diagram_code(self, diagram_type: str, requirement_text: str, context_history: list = None) -> str | None:
        """
        AIを使って図解コード(PlantUML, DOT等)を生成する。
        """
        # プロンプトテンプレートの選択
        prompt_template = config.DIAGRAM_PROMPT_TEMPLATES.get(diagram_type)
        if not prompt_template:
            logger.error(f"Unsupported diagram type for generation: {diagram_type}")
            return None

        # 文脈情報の構築
        history_text = ""
        if context_history:
            history_text = "\n".join([f"{msg.get('role')}: {str(msg.get('content'))[:200]}..." for msg in context_history])

        prompt = prompt_template.format(text=requirement_text)
        if history_text:
            prompt += f"\n\n## 補足コンテキスト (会話履歴)\n{history_text}"

        system_instruction = f"あなたは{diagram_type}記法の専門家です。構文エラーのない正確なコードを生成してください。"

        # Gemini呼び出し
        response_text, _ = generate_with_gemini(
            model_name=config.DIAGRAM_GEN_MODEL_NAME,
            contents=[types.Content(role="user", parts=[types.Part.from_text(text=prompt)])],
            system_instruction=system_instruction,
            max_output_tokens=65535,
            temperature=0.2, # コード生成なので低め
            user_info=self.user_info,
            feature_category="設計図解生成",
            skip_cosmos_log=False
        )

        if not response_text or response_text.startswith(("[エラー:", "[APIエラー:")):
            logger.error(f"Diagram generation failed: {response_text}")
            return None

        # コードブロック抽出
        return self._extract_code(response_text, diagram_type)

    def render_svg(self, diagram_type: str, code: str) -> str | None:
        """
        生成されたコードをSVG画像（またはHTML文字列）に変換する。
        """
        try:
            if diagram_type == 'plantuml':
                return self._render_plantuml(code)
            elif diagram_type == 'graphviz':
                return self._render_graphviz(code)
            elif diagram_type == 'svg':
                # SVG生コードの場合はそのまま返す（サニタイズが必要ならここで行う）
                # 基本的なチェックとして、<svg で始まり </svg> で終わるか確認
                code = code.strip()
                if code.startswith("<svg") and code.endswith("</svg>"):
                    return code
                else:
                    # 修正: コードブロックなどが残っている場合のクリーニング
                    match = re.search(r"<svg.*?</svg>", code, re.DOTALL)
                    if match:
                        return match.group(0)
                    logger.warning("Invalid SVG code format.")
                    return None
            elif diagram_type == 'html':
                # HTMLの場合はそのまま返す（フロントエンドでiframe等に埋め込む想定）
                return code
            elif diagram_type == 'mermaid':
                logger.error("Mermaid rendering is disabled.")
                return None
            else:
                logger.error(f"Unsupported diagram type for rendering: {diagram_type}")
                return None
        except Exception as e:
            logger.error(f"Render SVG error ({diagram_type}): {e}", exc_info=True)
            return None

    def _extract_code(self, text: str, lang: str) -> str:
        """Markdownコードブロックからコードを抽出"""
        # 言語指定ありのブロック (例: ```plantuml ... ```)
        pattern = rf"```{lang}\s*([\s\S]*?)\s*```"
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
            
        # 言語指定なしのブロック (例: ``` ... ```)
        match_generic = re.search(r"```\s*([\s\S]*?)\s*```", text)
        if match_generic:
             return match_generic.group(1).strip()
             
        # ブロックがない場合はテキスト全体を返す
        return text.strip()

    def _render_plantuml(self, code: str) -> str | None:
        """PlantUMLコードをSVGに変換 (subprocess呼び出し)"""
        jar_path = config.PLANTUML_JAR_PATH
        if not os.path.exists(jar_path):
            logger.error(f"PlantUML jar not found at: {jar_path}")
            return None
        
        try:
            # PlantUMLは標準入力からコードを受け取り、標準出力にSVGを出力可能（-pオプション）
            # ただし、日本語エンコーディングの問題を避けるため、一時ファイル経由を推奨
            with tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.puml', encoding='utf-8') as tmp_in:
                tmp_in.write(code)
                in_path = tmp_in.name
            
            # -tsvg: SVG出力, -nometadata: メタデータ削除, -charset UTF-8
            cmd = ['java', '-Djava.awt.headless=true', '-jar', jar_path, '-tsvg', '-nometadata', '-charset', 'UTF-8', in_path]
            
            # 実行 (標準出力には何も出ない仕様、同名の.svgファイルが生成される)
            subprocess.run(cmd, check=True, capture_output=True)
            
            out_path = in_path.replace('.puml', '.svg')
            if os.path.exists(out_path):
                with open(out_path, 'r', encoding='utf-8') as f:
                    svg = f.read()
                
                # 後始末
                os.remove(in_path)
                os.remove(out_path)
                return svg
            else:
                logger.error("PlantUML output file not generated.")
                if os.path.exists(in_path): os.remove(in_path)
                return None

        except subprocess.CalledProcessError as e:
            logger.error(f"PlantUML execution failed: {e.stderr.decode('utf-8', errors='ignore')}")
            if os.path.exists(in_path): os.remove(in_path)
            return None
        except Exception as e:
            logger.error(f"PlantUML render error: {e}")
            return None

    def _render_graphviz(self, code: str) -> str | None:
        """Graphviz(DOT)コードをSVGに変換 (subprocess呼び出し)"""
        dot_path = config.DOT_EXECUTABLE_PATH
        
        try:
            # dotコマンドは標準入力から読み込み、標準出力に出力可能
            # cmd: dot -Tsvg
            process = subprocess.Popen(
                [dot_path, '-Tsvg'],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True, 
                encoding='utf-8'
            )
            
            stdout, stderr = process.communicate(input=code)
            
            if process.returncode != 0:
                logger.error(f"Graphviz execution failed: {stderr}")
                return None
            
            return stdout

        except FileNotFoundError:
            logger.error(f"Graphviz executable not found at: {dot_path}")
            return None
        except Exception as e:
            logger.error(f"Graphviz render error: {e}")
            return None