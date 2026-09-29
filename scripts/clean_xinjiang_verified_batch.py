"""核验新疆兵团城市演示景点；默认预览，``--commit`` 才写库。"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from typing import Any

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config import get_settings  # noqa: E402
from backend.database import init_database, open_session  # noqa: E402
from backend.database.tables import AttractionRow, CityRow, ProvinceRow  # noqa: E402
from backend.rag.vector_store import ChromaAttractionIndex  # noqa: E402


PROVINCE = "新疆维吾尔自治区"


def item(*, city: str, name: str, new_poi_id: str, category: str, tags: list[str], duration: int, description: str, source_urls: list[str]) -> dict[str, Any]:
    return {"city": city, "name": name, "new_poi_id": new_poi_id, "category": category, "tags": tags, "duration": duration, "description": description, "source_urls": source_urls}


def deletion(*, city: str, name: str, reason: str, source_urls: list[str]) -> dict[str, Any]:
    return {"city": city, "name": name, "reason": reason, "source_urls": source_urls}


VERIFIED: dict[str, dict[str, Any]] = {
    "demo-659002-01": item(
        city="阿拉尔市", name="塔河源景区", new_poi_id="verified-xj-alar-tahe-source", category="滨水景观",
        tags=["自然", "滨水", "生态", "摄影"], duration=210,
        description="塔河源景区位于一师阿拉尔市十六团，是阿克苏河、和田河、叶尔羌河等水系汇聚形成塔里木河干流的生态景区，拥有胡杨林、湿地和观光设施，适合生态观光、滨水休闲与摄影。景区位置距阿拉尔城区较远，开放和交通以景区及兵团公告为准。",
        source_urls=["https://www.xjbt.gov.cn/c/2025-05-19/8407343.shtml", "https://www.xjbt.gov.cn/c/2026-09-08/8503070.shtml"]),
    "demo-659002-04": item(
        city="阿拉尔市", name="塔克拉玛干·沙漠之门景区", new_poi_id="verified-xj-alar-desert-gate", category="沙漠景观",
        tags=["自然", "沙漠", "休闲", "摄影"], duration=180,
        description="塔克拉玛干·沙漠之门景区位于一师阿拉尔市塔克拉玛干沙漠北缘，是国家5A级塔克拉玛干·三五九旅文化旅游区的核心板块，提供沙漠观光、户外体验和军垦文化旅游项目。沙漠活动受季节、天气和运营安排影响。",
        source_urls=["https://sjfw.mct.gov.cn/site/dataservice/culdetails?curId=10&id=35138", "https://www.tklmgsmzm.com/scenic/propagate/detail?id=58", "https://www.xjbt.gov.cn/c/2026-09-08/8503070.shtml"]),
    "demo-659006-01": item(
        city="铁门关市", name="丝路雄关景区", new_poi_id="verified-xj-tiemenguan-silk-pass", category="历史遗迹",
        tags=["历史文化", "人文", "古建", "城市漫游"], duration=150,
        description="丝路雄关景区位于第二师铁门关市，是以仿古建筑、丝路文化、军垦文化和园林景观为一体的国家3A级城市复合型景区，适合历史文化参观和城市漫游。开放区域与活动以景区公告为准。",
        source_urls=["https://tmg.gov.cn/zj/", "https://www.xjbt.gov.cn/c/2025-07-02/8419393.shtml"]),
    "demo-659008-01": item(
        city="可克达拉市", name="可克达拉草原", new_poi_id="verified-xj-kekedala-grassland", category="自然山水",
        tags=["自然", "草原", "摄影", "休闲"], duration=180,
        description="可克达拉草原位于第四师可克达拉市及其周边草原区域，是伊犁河谷城市与草原景观相连的自然空间，适合季节性草原观光、摄影和休闲。草原活动受季节、天气和牧区管理要求影响。",
        source_urls=["https://www.xjpcsc.gov.cn/article/bee354ab80814371b6d720dda9742789", "https://www.mct.gov.cn/wlbphone/wlbydd/xxfb/qglb/xjscjsbt/202410/t20241015_955711.html"]),
    "demo-659008-02": item(
        city="可克达拉市", name="可克达拉伊犁河生态景观带", new_poi_id="verified-xj-kekedala-yili-river", category="滨水景观",
        tags=["自然", "滨水", "生态", "城市漫游"], duration=150,
        description="可克达拉伊犁河生态景观带依托伊犁河可克达拉市段的河岸绿道和生态治理空间，适合滨水散步、观景和休闲。该景观带属于开放式城市生态空间，具体可游览路段以现场管理为准。",
        source_urls=["https://www.mee.gov.cn/home/ztbd/2023/mlhh2/yxal3/bt/202508/t20250818_1125558.shtml", "https://www.xjbt.gov.cn/c/2026-04-02/8477932.shtml"]),
    "demo-659008-03": item(
        city="可克达拉市", name="伊帕尔汗薰衣草观光园", new_poi_id="verified-xj-kekedala-lavender", category="生态农业",
        tags=["自然", "生态", "摄影", "休闲"], duration=150,
        description="伊帕尔汗薰衣草观光园位于可克达拉市，是以薰衣草种植、花海观光和香料文化体验为主的季节性旅游园区，适合花期摄影、休闲和农业观光。花期、开放时间与活动以园区公告为准。",
        source_urls=["https://sjfw.mct.gov.cn/site/dataservice/rural?type=143", "https://www.btzx.com.cn/web/2026/6/15/ARTI1781512079261707.html", "https://www.ctnews.com.cn/dongtai/content/2024-06/06/content_161086.html"]),
    "demo-659010-01": item(
        city="胡杨河市", name="胡杨河国家湿地公园", new_poi_id="verified-xj-huyanghe-wetland", category="生态动植物",
        tags=["自然", "湿地", "生态", "摄影"], duration=180,
        description="胡杨河国家湿地公园位于七师胡杨河市及周边团场，以奎屯河河道、胡杨林、沼泽湿地和水库湿地为主要生态资源，配套栈道、观鸟和休闲设施，适合生态观光与摄影。湿地保护区域和开放设施以当地管理规定为准。",
        source_urls=["https://www.xjbt.gov.cn/c/2026-05-13/8483934.shtml", "https://www.mee.gov.cn/ywgz/hjyxpj/jsxmhjyxpj/xmslqk/201909/W020190912592389246061.pdf"]),
    "demo-659010-02": item(
        city="胡杨河市", name="胡杨水韵景区", new_poi_id="verified-xj-huyanghe-huyang-shuiyun", category="滨水景观",
        tags=["自然", "滨水", "生态", "休闲"], duration=150,
        description="胡杨水韵景区依托胡杨河市团场水系和湿地景观，结合水产、花卉、果园及滨水休闲体验，适合短途生态观光和休闲。园区经营项目与开放时间以当地公告为准。",
        source_urls=["https://zhuanti.mct.gov.cn/csxz2022/xjbt/detail_g7yU_734/4502.html", "https://www.xjbt.gov.cn/c/2026-05-13/8483934.shtml"]),
}


DELETE: dict[str, dict[str, Any]] = {
    # 阿拉尔：误把塔什库尔干的帕米尔高原阿拉尔湿地公园写入阿拉尔市；另一条是沙漠之门的泛化重复名。
    "demo-659002-03": deletion(city="阿拉尔市", name="阿拉尔国家湿地公园", reason="公开资料所指的‘阿拉尔国家湿地公园’位于喀什州塔什库尔干县，并非阿拉尔市；无法确认该条为阿拉尔真实景点。", source_urls=["https://www.xjbt.gov.cn/c/2026-09-08/8503070.shtml", "https://sthjt.xinjiang.gov.cn/xjepd/hjyxpjsl/202306/38962bf28fc5425081035ee48e469a55/files/%E6%96%B0%E7%96%86%E5%96%80%E4%BB%80%E5%9C%B0%E5%8C%BA%E5%A1%94%E4%BB%80%E5%BA%93%E5%B0%94%E5%B9%B2%E5%8E%BF%E9%98%BF%E7%84%B6%E4%BF%9D%E6%B3%B0%E6%B0%B4%E5%BA%93%E5%B7%A5%E7%A8%8B%E5%85%AC%E7%A4%BA%E7%A8%BF.pdf"]),
    "demo-659002-05": deletion(city="阿拉尔市", name="塔克拉玛干沙漠边缘观景区", reason="未找到与该名称对应的独立开放景区，且空间上与已核验的沙漠之门景区高度重叠，删除泛化重复记录。", source_urls=["https://sjfw.mct.gov.cn/site/dataservice/culdetails?curId=10&id=35138", "https://www.tklmgsmzm.com/scenic/propagate/detail?id=58"]),
    # 图木舒克：公开资料确认的是屯垦历史博物馆、永安坝等具体资源，以下名称均未找到同名独立开放景点。
    "demo-659003-02": deletion(city="图木舒克市", name="图木舒克湿地公园", reason="未找到与该名称对应的官方景区或公园介绍，现有记录为泛化名称。", source_urls=["https://www.forestry.gov.cn/lyj/1/hxzltklmgsmbyzjz/20251128/651433.html", "https://xjbtnss.gov.cn/mobile/xwzx/tzgg/202303/W020230303698310329995.pdf"]),
    "demo-659003-03": deletion(city="图木舒克市", name="丝路屯垦文化园", reason="公开资料显示当地真实场馆为新疆屯垦历史博物馆，未找到同名‘丝路屯垦文化园’独立景区，删除无法确认的名称。", source_urls=["https://www.xjtklsbwg.com/", "https://zhuanti.mct.gov.cn/xjbt_detail/469.html"]),
    "demo-659003-04": deletion(city="图木舒克市", name="喀什河生态景观带", reason="检索到的同名官方景观带位于伊犁州尼勒克县，不在图木舒克市，删除异地错配记录。", source_urls=["https://www.xjnlk.gov.cn/xjnlk/c112921/201911/f9c6a4085c524caf9a7d5b8aa318896a.shtml"]),
    "demo-659003-05": deletion(city="图木舒克市", name="图木舒克城市规划展示馆", reason="未找到同名公开开放展馆的官方介绍，无法确认其为可规划旅游景点。", source_urls=["https://xjbtnss.gov.cn/mobile/xwzx/tzgg/202303/W020230303698310329995.pdf", "https://www.xjtklsbwg.com/"]),
    # 北屯：乌伦古湖黄金海岸和渔村均在福海县，其他两条未找到同名独立景点。
    "demo-659005-01": deletion(city="北屯市", name="乌伦古湖黄金海岸", reason="乌伦古湖黄金海岸景区位于阿勒泰地区福海县，不在北屯市，删除异地错配记录。", source_urls=["https://you.ctrip.com/sight/fuhai1446398/5635402.html", "https://sthjt.xinjiang.gov.cn/xjepd/hjyxpjsl/202412/075831bccbf34dbfa2f0dc5b55630950/files/%EF%BC%88%E9%80%81%E5%AE%A1%E7%A8%BF%EF%BC%89%E4%B9%8C%E4%BC%A6%E5%8F%A4%E6%B9%96%E7%94%9F%E6%80%81%E4%BF%AE%E5%A4%8D%E4%B8%8E%E6%B2%BB%E7%90%86%E9%A1%B9%E7%9B%AE%EF%BC%88%E6%95%B4%E6%9C%AC%EF%BC%89.pdf"]),
    "demo-659005-02": deletion(city="北屯市", name="额尔齐斯河国家湿地公园", reason="官方资料确认北屯有额尔齐斯河段和河谷湿地，但未找到名为‘额尔齐斯河国家湿地公园’的独立开放景区，删除不准确的公园名称。", source_urls=["https://www.btzx.com.cn/web/2026/6/3/ARTI1780453661648116.html", "https://btslj.xjbt.gov.cn/c/2026-08-18/8499981.shtml"]),
    "demo-659005-03": deletion(city="北屯市", name="北屯市滨河公园", reason="官方资料介绍北屯城市绿化和河湖生态空间，但未找到该名称对应的独立景点条目，删除泛化公共空间记录。", source_urls=["https://www.huyangnet.cn/content/2023-07/12/content_1797984.html", "https://btslj.xjbt.gov.cn/c/2026-08-18/8499981.shtml"]),
    "demo-659005-04": deletion(city="北屯市", name="乌伦古湖渔村", reason="乌伦古湖渔业和渔村旅游资源主要位于福海县湖区，未确认北屯市存在该同名景点，删除异地/泛化记录。", source_urls=["https://you.ctrip.com/sight/fuhai1446398/5635402.html", "https://sthjt.xinjiang.gov.cn/xjepd/hjyxpjsl/202412/075831bccbf34dbfa2f0dc5b55630950/files/%EF%BC%88%E9%80%81%E5%AE%A1%E7%A8%BF%EF%BC%89%E4%B9%8C%E4%BC%A6%E5%8F%A4%E6%B9%96%E7%94%9F%E6%80%81%E4%BF%AE%E5%A4%8D%E4%B8%8E%E6%B2%BB%E7%90%86%E9%A1%B9%E7%9B%AE%EF%BC%88%E6%95%B4%E6%9C%AC%EF%BC%89.pdf"]),
    "demo-659005-05": deletion(city="北屯市", name="北屯兵团文化展馆", reason="未找到同名公开开放展馆的官方介绍，无法确认其为独立旅游景点。", source_urls=["https://www.beitunsw.cn/", "https://www.btzx.com.cn/web/2026/6/3/ARTI1780453661648116.html"]),
    # 铁门关：仅保留官方明确的丝路雄关景区，其余是未证实的子项目或泛化名称。
    "demo-659006-02": deletion(city="铁门关市", name="孔雀河生态景观带", reason="未找到铁门关市同名独立景观带的公开景区资料，删除泛化河岸记录。", source_urls=["https://tmg.gov.cn/zj/", "https://www.xjbt.gov.cn/c/2025-07-02/8419393.shtml"]),
    "demo-659006-03": deletion(city="铁门关市", name="铁门关兵团文化公园", reason="官方城市规划资料仅确认综合公园和丝路雄关等具体资源，未确认该同名景点，删除泛化名称。", source_urls=["https://www.tmg.gov.cn/gk/fdzdgknr/zcfg/zcwj/171154.htm", "https://tmg.gov.cn/zj/"]),
    "demo-659006-04": deletion(city="铁门关市", name="古丝路关隘体验区", reason="未找到同名独立开放景区，且内容应属于丝路雄关景区的概念性子项目，删除父子景点重叠记录。", source_urls=["https://tmg.gov.cn/zj/", "https://www.xjbt.gov.cn/c/2025-07-02/8419393.shtml"]),
    "demo-659006-05": deletion(city="铁门关市", name="铁门关城市展馆", reason="未找到同名城市展馆的公开开放信息，无法确认景点真实性。", source_urls=["https://www.tmg.gov.cn/gk/fdzdgknr/zcfg/zcwj/171154.htm", "https://tmg.gov.cn/zj/"]),
    # 可克达拉：惠远古城属霍城县；城市公园为泛化名称。
    "demo-659008-04": deletion(city="可克达拉市", name="惠远古城文化体验区", reason="惠远古城位于伊犁州霍城县，不在可克达拉市，删除异地错配记录。", source_urls=["https://www.xinjiang.gov.cn/xinjiang/uploadfiles/2019120614162036026a.pdf", "https://wlt.xinjiang.gov.cn/wlt/lyzy/202203/2c4b9a8b1fd54d149e1f0bf9d0b1e77c.shtml"]),
    "demo-659008-05": deletion(city="可克达拉市", name="可克达拉城市公园", reason="公开资料确认可克达拉有多个城市公园，但未确认该泛化名称对应的独立公园，删除无法定位的公共空间记录。", source_urls=["https://www.ctnews.com.cn/dongtai/content/2024-06/06/content_161086.html", "https://www.xjbt.gov.cn/c/2026-04-02/8477932.shtml"]),
    # 昆玉：公开资料中的真实资源为红枣文化馆、进军和田纪念馆等，以下名称均未找到同名景点。
    "demo-659009-01": deletion(city="昆玉市", name="昆玉湖生态景区", reason="未找到同名官方景区资料，现有名称与坐标无法核实。", source_urls=["https://www.btdsss.gov.cn/zjsss/", "https://zhuanti.mct.gov.cn/csxz2022/xjbt/detail_g7yU_734/4503.html"]),
    "demo-659009-02": deletion(city="昆玉市", name="玉泉山景区", reason="公开检索到的‘玉泉山’主要位于北京市海淀区，未找到昆玉市同名景区，删除异地错配记录。", source_urls=["https://www.bjhd.gov.cn/ztzx/2022/hdwl/hdwhcc/sswy/202212/t20221202_4577168.shtml", "https://www.btdsss.gov.cn/zjsss/"]),
    "demo-659009-03": deletion(city="昆玉市", name="兵团屯垦文化园", reason="未找到同名官方开放景区；昆玉真实屯垦文化资源为红枣文化馆、印象二连等具体场馆，删除泛化名称。", source_urls=["https://zhuanti.mct.gov.cn/csxz2022/xjbt/detail_g7yU_734/4503.html", "https://www.btdsss.gov.cn/zjsss/"]),
    "demo-659009-04": deletion(city="昆玉市", name="和田河生态景观带", reason="未找到昆玉市同名独立景观带的官方旅游资料，删除无法定位的泛化记录。", source_urls=["https://www.xjbt.gov.cn/c/2020-04-09/8035873.shtml", "https://www.btdsss.gov.cn/zjsss/"]),
    "demo-659009-05": deletion(city="昆玉市", name="昆玉市博物馆", reason="未找到同名公开开放博物馆信息；当地公开文旅资源为进军和田纪念馆等具体场馆，删除未核实记录。", source_urls=["https://www.btdsss.gov.cn/zjsss/", "https://zhuanti.mct.gov.cn/csxz2022/xjbt/detail_g7yU_734/4503.html"]),
    # 胡杨河：公开资料确认的是胡杨河国家湿地公园、胡杨水韵等具体资源，其余为未核实泛化名称。
    "demo-659010-03": deletion(city="胡杨河市", name="兵团农垦博物馆", reason="‘新疆兵团农垦博物馆’公开资料指向石河子市，不在胡杨河市，删除异地错配记录。", source_urls=["https://zh.wikipedia.org/wiki/%E6%96%B0%E7%96%86%E5%85%B5%E5%9B%A2%E5%86%9C%E5%9E%A6%E5%8D%9A%E7%89%A9%E9%A6%86", "https://www.xjbt.gov.cn/c/2026-05-13/8483934.shtml"]),
    "demo-659010-04": deletion(city="胡杨河市", name="胡杨河田园观光区", reason="未找到该名称对应的独立开放景区；公开资料仅确认团场农业休闲项目，删除泛化记录。", source_urls=["https://zhuanti.mct.gov.cn/csxz2022/xjbt/detail_g7yU_734/4502.html", "https://www.xjbt.gov.cn/c/2026-05-13/8483934.shtml"]),
    "demo-659010-05": deletion(city="胡杨河市", name="城市绿洲休闲带", reason="未找到同名独立景点，属于无法定位的规划/泛化景观名称，删除。", source_urls=["https://www.xjbt.gov.cn/c/2026-05-13/8483934.shtml", "https://www.mee.gov.cn/ywgz/hjyxpj/jsxmhjyxpj/xmslqk/201909/W020190912592389246061.pdf"]),
    # 新星：东天山、星星峡等不在新星市，其他名称未确认。
    "demo-659011-01": deletion(city="新星市", name="东天山景观带", reason="东天山旅游资源主要位于哈密市及巴里坤等地，未找到新星市同名独立景观带。", source_urls=["https://www.btnsss.gov.cn/zjsss/qcgk/74171.htm", "https://www.xinjiang.gov.cn/xinjiang/uploadfiles/2019120614162036026a.pdf"]),
    "demo-659011-02": deletion(city="新星市", name="新星市田园观光区", reason="未找到同名官方景区或开放观光区，删除泛化名称。", source_urls=["https://www.btnsss.gov.cn/zjsss/qcgk/74171.htm", "https://www.btnsss.gov.cn/"]),
    "demo-659011-03": deletion(city="新星市", name="兵团屯垦文化园", reason="官方资料确认红星二场军垦文化体验营和军垦博物馆等具体资源，未确认该泛化名称为独立景区。", source_urls=["https://www.btnsss.gov.cn/ywdt/qcdt/108473.htm", "https://www.btnsss.gov.cn/zjsss/qcgk/74171.htm"]),
    "demo-659011-04": deletion(city="新星市", name="星星峡丝路驿站", reason="星星峡属于哈密市伊州区方向，未确认其为新星市景点，删除异地错配记录。", source_urls=["https://www.btnsss.gov.cn/zjsss/qcgk/74171.htm", "https://www.xinjiang.gov.cn/xinjiang/uploadfiles/2019120614162036026a.pdf"]),
    "demo-659011-05": deletion(city="新星市", name="新星市城市公园", reason="未找到该泛化名称对应的独立城市公园条目，删除无法定位的公共空间记录。", source_urls=["https://www.btnsss.gov.cn/zjsss/qcgk/74171.htm", "https://www.btnsss.gov.cn/"]),
    # 白杨：公开资料仅能确认河流、湿地和边境草原等资源，未找到这些泛化名称对应的独立开放景点。
    "demo-659012-01": deletion(city="白杨市", name="白杨市边境草原", reason="未找到同名独立草原景区的官方介绍，删除无法定位的泛化名称。", source_urls=["https://www.btdjs.gov.cn/", "https://www.xjtc.gov.cn/zjtc/tcgk"]),
    "demo-659012-02": deletion(city="白杨市", name="塔城河生态景观带", reason="塔城地区主要河流为额敏河、白杨河等，未找到‘塔城河生态景观带’这一独立景点名称，删除名称混淆记录。", source_urls=["https://www.xjtc.gov.cn/zjtc/tcgk", "https://www.xjtc.gov.cn/zzb/bmdt1__ywdt/jrtc/bmdt/content_48059"]),
    "demo-659012-03": deletion(city="白杨市", name="兵团文化展馆", reason="未找到同名公开开放展馆信息，无法确认其为独立旅游景点。", source_urls=["https://www.btdjs.gov.cn/", "https://www.xjtc.gov.cn/zjtc/tcgk"]),
    "demo-659012-04": deletion(city="白杨市", name="额敏河湿地观景区", reason="官方规划提及额敏河湿地生态修复，但未确认同名开放景区，删除将规划资源当作景点的记录。", source_urls=["https://xjtc.gov.cn/upload/main/infopublicity/publicinformation/file/2024/09/14/202409141809560701.pdf", "https://www.xjtc.gov.cn/zzb/bmdt1__ywdt/jrtc/bmdt/content_48059"]),
    "demo-659012-05": deletion(city="白杨市", name="白杨市田园休闲区", reason="未找到同名官方开放景区，删除泛化规划名称。", source_urls=["https://www.btdjs.gov.cn/", "https://www.xjtc.gov.cn/zjtc/tcgk"]),
}


def _backup_path(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"xinjiang-verified-batch-before-{stamp}.json"
    suffix = 1
    while path.exists():
        path = directory / f"xinjiang-verified-batch-before-{stamp}-{suffix}.json"
        suffix += 1
    return path


def _payload(row: AttractionRow) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def clean(*, commit: bool, backup_dir: Path) -> dict[str, Any]:
    init_database()
    target_ids = tuple([*VERIFIED, *DELETE])
    with open_session() as session:
        pairs = list(
            session.execute(
                select(AttractionRow, CityRow)
                .join(CityRow, CityRow.id == AttractionRow.city_id)
                .join(ProvinceRow, ProvinceRow.id == CityRow.province_id)
                .where(ProvinceRow.name == PROVINCE, AttractionRow.poi_id.in_(target_ids))
                .order_by(AttractionRow.poi_id)
            ).all()
        )
        found = {row.poi_id: (row, city.name) for row, city in pairs}
        missing = sorted(set(target_ids) - set(found))
        mismatches = []
        aliases = {
            "demo-659002-01": {"塔里木河生态旅游区", "塔河源景区"},
            "demo-659002-04": {"沙漠之门景区", "塔克拉玛干·沙漠之门景区"},
            "demo-659006-01": {"铁门关景区", "丝路雄关景区"},
            "demo-659008-02": {"伊犁河谷生态观景带", "可克达拉伊犁河生态景观带"},
            "demo-659008-03": {"紫色花海景区", "伊帕尔汗薰衣草观光园"},
            "demo-659010-01": {"胡杨河胡杨林", "胡杨河国家湿地公园"},
            "demo-659010-02": {"奎屯河生态公园", "胡杨水韵景区"},
        }
        for poi_id, spec in {**VERIFIED, **DELETE}.items():
            if poi_id in found:
                row, city = found[poi_id]
                allowed_names = aliases.get(poi_id, {spec["name"]})
                if row.name not in allowed_names:
                    mismatches.append({"poi_id": poi_id, "expected_name": sorted(allowed_names), "actual_name": row.name})
                if city != spec["city"]:
                    mismatches.append({"poi_id": poi_id, "expected_city": spec["city"], "actual_city": city})
        new_ids = tuple(spec["new_poi_id"] for spec in VERIFIED.values())
        conflicting = list(session.scalars(select(AttractionRow.poi_id).where(AttractionRow.poi_id.in_(new_ids))))
        if missing or mismatches or conflicting:
            raise RuntimeError(json.dumps({"missing": missing, "mismatches": mismatches, "new_id_conflicts": conflicting}, ensure_ascii=False))
        verified_rows = [found[poi_id][0] for poi_id in VERIFIED]
        deleted_rows = [found[poi_id][0] for poi_id in DELETE]
        preview = {
            "verified": [{"poi_id": row.poi_id, "city": found[row.poi_id][1], "old_name": row.name, "new_name": VERIFIED[row.poi_id]["name"], "action": "verify"} for row in verified_rows],
            "deleted": [{"poi_id": row.poi_id, "city": found[row.poi_id][1], "name": row.name, "reason": DELETE[row.poi_id]["reason"], "action": "delete"} for row in deleted_rows],
        }
        if not commit:
            return {"province": PROVINCE, "mode": "preview", "verified_replacements": len(verified_rows), "deletions": len(deleted_rows), "affected": preview, "source_urls": sorted({url for spec in {**VERIFIED, **DELETE}.values() for url in spec["source_urls"]}), "message": "仅预览；传入 --commit 才会备份并写入。未调用高德。"}
        backup_path = _backup_path(backup_dir)
        backup_path.write_text(json.dumps({"created_at": datetime.now().isoformat(timespec="seconds"), "province": PROVINCE, "reason": "public-source-verified-xinjiang-corps-batch", "rows": [_payload(row) for row in [*verified_rows, *deleted_rows]], "verified_sources": {poi_id: spec["source_urls"] for poi_id, spec in {**VERIFIED, **DELETE}.items()}, "decisions": {poi_id: ("verify" if poi_id in VERIFIED else "delete") for poi_id in target_ids}}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        for old_poi_id, spec in VERIFIED.items():
            row = found[old_poi_id][0]
            row.poi_id = spec["new_poi_id"]
            row.name = spec["name"]
            row.category = spec["category"]
            row.tags = spec["tags"]
            row.description = spec["description"]
            row.visit_duration_minutes = spec["duration"]
        for row in deleted_rows:
            session.delete(row)
        session.commit()
    vectors_deleted = 0
    vector_error = None
    try:
        settings = get_settings()
        index = ChromaAttractionIndex(settings.chroma_path, collection_name=settings.chroma_collection)
        index.collection.delete(ids=list(target_ids))
        vectors_deleted = len(target_ids)
    except Exception as exc:  # pragma: no cover
        vector_error = f"{type(exc).__name__}: {exc}"
    return {"province": PROVINCE, "mode": "commit", "verified_replacements": len(verified_rows), "deletions": len(deleted_rows), "affected": preview, "backup_path": str(backup_path), "vectors_deleted": vectors_deleted, "vector_error": vector_error, "message": "新疆兵团城市公开网页核验结果已写入；未调用高德，未新增数据库字段。"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--backup-dir", type=Path, default=ROOT / "data" / "backups")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(clean(commit=args.commit, backup_dir=args.backup_dir), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
