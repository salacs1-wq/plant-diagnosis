# weeds_logic.py

def build_weed_summary(mapped_result):
    """
    Egységes belső gyom-logikai összefoglaló.
    A mapperelt PlantNet eredményből ad vissza egy egyszerű,
    később bővíthető szerkezetet.
    """

    if not mapped_result:
        return {
            "top1": None,
            "top5": [],
            "has_results": False
        }

    top1 = mapped_result.get("top1")
    top5 = mapped_result.get("top5", [])

    return {
        "top1": top1,
        "top5": top5,
        "has_results": len(top5) > 0
    }
