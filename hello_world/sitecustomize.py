try:
    import inqsi_api

    def _scan_upload_by_id(upload_id):
        table = inqsi_api._snapshots_table()
        start_key = None
        while True:
            args = {
                "FilterExpression": "record_type = :rt AND upload_id = :uid",
                "ExpressionAttributeValues": {":rt": "member_image_upload", ":uid": upload_id},
            }
            if start_key:
                args["ExclusiveStartKey"] = start_key
            result = table.scan(**args)
            items = result.get("Items") or []
            if items:
                return items[0]
            start_key = result.get("LastEvaluatedKey")
            if not start_key:
                return None

    inqsi_api._scan_upload_by_id = _scan_upload_by_id
except Exception:
    pass
