"""用天津市文旅公开资料替换天津景点模板简介。默认预览。"""

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
from backend.database.tables import AttractionRow  # noqa: E402
from backend.rag.vector_store import ChromaAttractionIndex  # noqa: E402


SOURCE_URLS = [
    "https://whly.tj.gov.cn/TJSWHHLYJ/gabsycs/wlmlgh/",
    "https://whly.tj.gov.cn/tjswlzxw/ztzl1/zthgc/sshzjcjn/202501/t20250118_6837846.html",
    "https://whly.tj.gov.cn/tjswlzxw/lytj/lyjg/xcsshfg/202501/t20250124_6844407.html",
    "https://s.visitbeijing.com.cn/attraction/118514",
    "https://s.visitbeijing.com.cn/attraction/118479",
    "https://whly.tj.gov.cn/TJSWHHLYJ/gabsycs/mtjjgh/202407/t20240723_6684897.html",
]


def _text(body: str) -> str:
    return f"{body}开放时间、预约和临时管控以景区官方公告为准。"


VERIFIED: dict[str, str] = {
    "B0FFFAGQTX": _text("世纪钟广场位于天津站及海河沿线，以世纪钟城市地标、站城公共空间和海河景观为主要体验，适合短时城市漫游。"),
    "B0FFJHVIUA": _text("东疆亲海公园位于天津东疆片区，以海岸线、亲水步道和城市滨海休闲空间为主要内容，适合散步和摄影。"),
    "B0GR06UINT": _text("东疆理想海岸沙滩景区以人工沙滩、海岸步道和滨海休闲活动为主要体验，适合亲海、观景和短时休闲。"),
    "B0FFG98JS1": _text("中新生态城妈祖文化园以妈祖信俗、滨水园林和文化展示为主要内容，适合人文参观与城市休闲。"),
    "B00160E2UZ": _text("九龙山国家森林公园以山地森林、步道和近郊生态景观为主要内容，适合登山、自然观察和摄影。"),
    "B0HAFZTHJ8": _text("吉姆冒险世界以户外探险、亲子活动和运动体验为主要内容，适合家庭游客按项目选择半日休闲。"),
    "B00160DFJK": _text("天主教天津教区西开总堂是天津近代宗教建筑代表，哥特式建筑形制和城市历史共同构成参观内容。"),
    "mct-4a-1920": _text("天津东疆湾景区以人工沙滩、海岸线和滨海休闲活动为主要内容，是天津重要的亲海旅游空间。"),
    "B0IUAARMIR": _text("天津临港海岸线公园以海岸景观、滨水步道和临港城市开放空间为主要内容，适合散步和摄影。"),
    "B00160E5VJ": _text("天津五大道文化旅游区保存大量近代洋楼和历史街区空间，适合建筑观察、城市漫游和近代天津历史体验。"),
    "B00160D2AW": _text("天津动物园以动物展示、科普教育和园林游览为主要内容，适合亲子和自然观察。"),
    "B00160DB75": _text("天津古文化街以仿明清街巷、天后宫、传统商贸、民俗和非遗业态为主要内容，是天津城市文化地标。"),
    "mct-5a-34789": _text("天津古文化街旅游区（津门故里）以传统街巷、天后宫、民俗文化、特色商业和天津地方生活为主要体验。"),
    "mct-4a-829": _text("天津周恩来邓颖超纪念馆以两位革命家的生平事迹、文物和史料展陈为主要内容，适合红色文化参观与研学。"),
    "mct-4a-967": _text("天津天塔湖风景区以天塔地标、湖面园林和城市观景为主要内容，适合建筑摄影与城市休闲。"),
    "mct-4a-431": _text("天津市北辰区天士力大健康城景区以中药现代化、大健康产业展示和企业文化体验为主要内容，适合工业与科普研学。"),
    "mct-4a-2190": _text("天津市和平区五大道文化旅游区以历史洋楼、街区道路和近代城市生活遗存为主要内容，适合步行漫游和建筑摄影。"),
    "mct-4a-375": _text("天津市和平区金街文化旅游区以和平路商业街、近代建筑、城市消费和夜间漫游为主要内容。"),
    "mct-4a-2974": _text("天津市天山海世界米立方水上乐园以室内外水上娱乐、亲子戏水和主题游乐设施为主要内容。"),
    "mct-4a-823": _text("天津市武清区南湖绿博园景区以湖泊、园林展园、绿地和休闲步道为主要内容，适合自然观赏和亲子漫游。"),
    "mct-4a-280": _text("天津市河北区北宁公园以湖面、园林、桥亭和城市公共休闲空间为主要内容，适合散步和摄影。"),
    "mct-4a-1516": _text("天津市河北区大悲禅院是天津重要佛教寺院，寺院建筑、宗教文化和城市历史构成主要参观内容。"),
    "mct-4a-2623": _text("天津市河北区天津之眼摩天轮景区以摩天轮、海河沿岸夜景和城市观景为主要体验，适合夜游和摄影。"),
    "mct-4a-3396": _text("天津市滨海新区塘沽大沽口炮台遗址博物馆以大沽口炮台遗址、海防历史和文物展陈为主要内容，适合历史研学。"),
    "mct-4a-2191": _text("天津市红桥区平津战役纪念馆以平津战役历史、文物、图片和军事史料展陈为主要内容，适合红色文化教育。"),
    "mct-4a-566": _text("天津市蓟州区梨木台景区以山地森林、峡谷、溪流和岩石景观为主要内容，适合自然观赏和轻徒步。"),
    "B00160F6ZS": _text("天津意大利风情旅游区保存近代意式建筑和街区空间，集合历史建筑、餐饮和城市夜游体验。"),
    "B0JDO7EEKK": _text("天津方特旅游度假区以主题游乐、演艺、亲子项目和度假休闲为主要内容，适合安排整块半日或一日行程。"),
    "B0FFF3523J": _text("天津方特欢乐世界以大型主题游乐设施、演艺和亲子娱乐为主要内容，适合主题娱乐和家庭出游。"),
    "B001605O2L": _text("天津水上公园以湖泊水面、园林步道和城市休闲设施为主要内容，适合散步、划船和摄影。"),
    "B001611WYT": _text("天津泰达航母主题公园以退役航母展示、军事科普、海洋文化和主题游乐为主要内容。"),
    "B00160E3FW": _text("天津盘山风景名胜区以山水、佛教寺院、古塔和皇家游踪等自然与人文景观著称，是国家5A级旅游景区。"),
    "B00160DRHL": _text("小白楼是天津近代城市街区和商业文化地标，适合结合周边历史建筑进行城市漫游。"),
    "B0FFHTLSUE": _text("小白楼1902欧式风情街以近代欧式建筑、餐饮商业和街区夜游为主要体验，适合建筑摄影和城市休闲。"),
    "B0016162R9": _text("张园是天津近代名人故居和历史建筑空间，适合近代城市史、建筑和人文参观。"),
    "B00160E52E": _text("张学良故居保存近代名人居住建筑及相关历史陈列，适合天津近代史和建筑文化参观。"),
    "B0FFGUGCT6": _text("杨柳青古镇以年画、传统民居、运河文化和古镇街巷为主要体验，适合民俗、人文和慢游。"),
    "B00160DTL1": _text("梨木台风景区以山地森林、峡谷、溪流和天然岩体为主要景观，适合自然观赏和轻徒步。"),
    "B0FFF3IW8X": _text("民园广场位于五大道历史街区，以开放广场、近代建筑和城市公共活动为主要内容，适合短时休闲和街区漫游。"),
    "B0KUFCYV3J": _text("津一 PARK 是天津城市休闲与商业体验空间，适合短时休息、餐饮和城市漫游。"),
    "B00160EZX5": _text("海合安天津极地海洋公园以极地和海洋动物展示、科普教育及亲子互动为主要内容。"),
    "B0016127V6": _text("海河游船（天津站码头）以海河两岸近代建筑、桥梁和城市夜景观光为主要体验，适合滨水摄影和夜游。"),
    "B0FFF5TRL3": _text("滨海鲤鱼门以滨海餐饮、渔港文化和海岸休闲为主要体验，适合结合滨海新区线路安排。"),
    "B001605643": _text("独乐寺是天津蓟州重要古建筑和佛教文化遗产，山门、观音阁等辽代建筑构成主要参观内容。"),
    "mct-5a-34790": _text("盘山风景名胜区以山水、名胜古迹、佛教寺院和皇家园林遗存为主要内容，是天津蓟州的国家5A级景区。"),
    "B001605KTI": _text("黄崖关长城风景区位于天津蓟州北部，长城、关隘、敌台和古代军事防御遗存构成主要景观，是世界文化遗产组成部分。"),
}


def _payload(row: AttractionRow) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def clean(*, commit: bool, backup_dir: Path) -> dict[str, Any]:
    init_database()
    with open_session() as session:
        rows = list(session.scalars(select(AttractionRow).where(AttractionRow.poi_id.in_(tuple(VERIFIED))).order_by(AttractionRow.id)))
        found = {row.poi_id: row for row in rows}
        missing = sorted(set(VERIFIED) - set(found))
        if missing:
            raise RuntimeError(json.dumps({"missing": missing}, ensure_ascii=False))
        changed = [{"poi_id": row.poi_id, "name": row.name, "before": row.description, "after": VERIFIED[row.poi_id]} for row in rows if row.description != VERIFIED[row.poi_id]]
        if not commit:
            return {"province": "天津市", "mode": "preview", "records": len(rows), "changed": len(changed), "sources": SOURCE_URLS, "sample": changed[:5], "message": "仅预览；传入 --commit 才写入。"}
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"tianjin-descriptions-before-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        backup_path.write_text(json.dumps({"created_at": datetime.now().isoformat(timespec="seconds"), "province": "天津市", "source_urls": SOURCE_URLS, "rows": [_payload(row) for row in rows]}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        for row in rows:
            row.description = VERIFIED[row.poi_id]
        session.commit()

    vectors_deleted = 0
    vector_error = None
    try:
        settings = get_settings()
        index = ChromaAttractionIndex(settings.chroma_path, collection_name=settings.chroma_collection)
        index.collection.delete(ids=list(VERIFIED))
        vectors_deleted = len(VERIFIED)
    except Exception as exc:  # pragma: no cover
        vector_error = f"{type(exc).__name__}: {exc}"
    return {"province": "天津市", "mode": "commit", "records": len(rows), "changed": len(changed), "backup_path": str(backup_path), "vectors_deleted": vectors_deleted, "vector_error": vector_error, "sources": SOURCE_URLS, "message": "已用公开天津文旅资料替换简介；未调用高德、未新增字段。"}


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
