def map_plantnet_result(data):
    """
    PlantNet API válasz -> belső egységes forma
    """

    if not data:
        return None

    results = data.get("results", [])

    if not results:
        return None

    mapped = []

    for r in results[:5]:
        species = r.get("species", {})

        latin = species.get("scientificNameWithoutAuthor", "")
        common = species.get("commonNames", [])

        score = r.get("score", 0)

        mapped.append({
            "latin_name": latin,
            "common_name": common[0] if common else "",
            "score": score
        })

    return {
        "top1": mapped[0] if mapped else None,
        "top5": mapped
    }
