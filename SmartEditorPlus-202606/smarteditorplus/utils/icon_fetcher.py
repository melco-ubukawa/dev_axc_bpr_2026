# smarteditorplus/utils/icon_fetcher.py

import requests
import xml.etree.ElementTree as ET

def get_icon_svg(icon_set: str, icon_name: str, color: str | None = None, size: int | None = None) -> str:
    """
    Iconify APIからアイコンのSVGデータを取得し、色とサイズを調整する。
    取得に失敗した場合は、エラーを示す代替SVGを返す。

    Args:
        icon_set (str): アイコンセット名 (例: "mdi", "fa-solid").
        icon_name (str): アイコン名 (例: "account-circle", "star").
        color (str | None): アイコンの色 (例: "#FF5733", "gold"). デフォルトはNone.
        size (int | None): アイコンのサイズ (幅と高さ, px単位). デフォルトはNone (元のサイズを維持).

    Returns:
        str: 調整済みのSVG文字列。取得失敗時はエラーアイコンのSVG文字列。
    """
    def get_fallback_svg(error_size: int | None) -> str:
        """エラー時に表示する代替SVGを生成する"""
        display_size = error_size or 24  # サイズ指定がなければ24px
        # Material Design Icons の "error" アイコン
        return f'<svg width="{display_size}" height="{display_size}" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path fill="red" d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10s10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/></svg>'

    url = f"https://api.iconify.design/{icon_set}/{icon_name}.svg"
    try:
        # APIにリクエストを送信 (タイムアウトを設定)
        response = requests.get(url, timeout=5)
        
        # 4xx (Client Error) や 5xx (Server Error) の場合に例外を発生させる
        response.raise_for_status()
        
        svg_content = response.text

        # SVGはXMLなので、XMLパーサーで扱う
        ET.register_namespace('', "http://www.w3.org/2000/svg")
        root = ET.fromstring(svg_content)

        if size:
            root.set('width', str(size))
            root.set('height', str(size))
        else:
            # 既存のwidth/height属性があれば削除する
            if 'width' in root.attrib:
                del root.attrib['width']
            if 'height' in root.attrib:
                del root.attrib['height']
        
        # 色が指定されていれば、<path>要素のfill属性またはstroke属性を上書き
        if color:
            for path in root.findall('.//{http://www.w3.org/2000/svg}path'):
                # 既にfill属性があるか、またはstrokeがnoneでない場合に色を適用
                if 'fill' in path.attrib and path.attrib['fill'] != 'none':
                    path.set('fill', color)
                elif 'stroke' in path.attrib and path.attrib['stroke'] != 'none':
                     # fillがない場合はstrokeに色を適用してみる
                     path.set('stroke', color)

        # 変更されたSVGツリーを文字列として返す
        return ET.tostring(root, encoding='unicode')

    except requests.exceptions.RequestException as e:
        # ネットワークエラーやHTTPエラーステータスの場合
        print(f"アイコン '{icon_set}:{icon_name}' の取得に失敗しました: {e}")
        # 失敗した場合、代替SVGを返す
        return get_fallback_svg(size)
    except ET.ParseError as e:
        # APIから返されたSVGが不正な形式だった場合
        print(f"アイコン '{icon_set}:{icon_name}' のSVGパースに失敗しました: {e}")
        return get_fallback_svg(size)
    except Exception as e:
        # その他の予期せぬエラー
        print(f"アイコン '{icon_set}:{icon_name}' の処理中に予期せぬエラーが発生しました: {e}")
        return get_fallback_svg(size)