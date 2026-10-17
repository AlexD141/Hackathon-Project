import json

with open("cookingUtensils.json") as f:
    data = json.load(f)

items = []
for category in data["categories"].values():
    for item in category:
        if item["name"] not in items:
            items.append(item["name"])

#print(items)
#print(len(items), "items")

with open("kitchen_utensils.txt", "w") as f:
    f.write("\n".join(items))