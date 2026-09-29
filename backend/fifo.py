def fifo_outbound(batches, requested_quantity):
    """
    依照進貨時間進行 FIFO 出貨。

    batches: 庫存批次資料
    requested_quantity: 要出貨的數量
    """

    # 最早進貨的批次排在最前面
    batches.sort(key=lambda batch: batch["received_at"])

    remaining = requested_quantity
    outbound_records = []

    for batch in batches:
        if remaining <= 0:
            break

        # 這個批次實際需要拿多少
        take_quantity = min(batch["quantity"], remaining)

        # 扣除庫存
        batch["quantity"] -= take_quantity
        remaining -= take_quantity

        outbound_records.append({
            "batch_id": batch["batch_id"],
            "location": batch["location"],
            "quantity": take_quantity
        })

    # 庫存不足
    if remaining > 0:
        raise ValueError("庫存不足，無法完成出貨")

    return outbound_records