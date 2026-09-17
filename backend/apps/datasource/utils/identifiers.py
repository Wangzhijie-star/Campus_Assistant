import hashlib


def postgres_column_names(columns):
    """Keep short names intact and fit unique names into PostgreSQL's 63 bytes."""
    originals = [str(column) for column in columns]
    reserved = {name for name in originals if len(name.encode('utf-8')) <= 63}
    used = set()
    result = []
    for name in originals:
        candidate = name
        attempt = 0
        while len(candidate.encode('utf-8')) > 63 or candidate in used:
            digest = hashlib.sha256(name.encode('utf-8')).hexdigest()[:10]
            suffix = f'_{digest}' if attempt == 0 else f'_{digest}_{attempt}'
            prefix = name.encode('utf-8')[:63 - len(suffix)].decode('utf-8', errors='ignore')
            candidate = prefix + suffix
            attempt += 1
            if candidate in reserved:
                candidate = name
        used.add(candidate)
        result.append(candidate)
    return result
