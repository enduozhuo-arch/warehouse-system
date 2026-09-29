def update_spoilage(batch, spoiled_quantity):
    """
    盤點時扣除腐爛商品數量。

    batch: 要盤點的庫存批次
    spoiled_quantity: 腐爛數量
    """

    if spoiled_quantity < 0:
        raise ValueError("腐爛數量不能小於 0")

    if spoiled_quantity > batch["qty"]:
        raise ValueError("腐爛數量不能大於目前庫存")

    batch["qty"] -= spoiled_quantity

    return {
        "id": batch["id"],
        "spoiled_quantity": spoiled_quantity,
        "qty": batch["qty"]
    }