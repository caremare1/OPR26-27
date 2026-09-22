import requests
import pprint
import random
urlTrivia = "https://opentdb.com/api.php?amount=5&difficulty=medium&type=multiple"

sez = []
call = requests.get(urlTrivia).json()
pprint.pprint(call["results"])
rez = call["results"]
random.shuffle(rez)

for r in rez:
    sez.append(r["question"])
    sez.append(r["incorrect_answers"])
    sez.append(r["correct_answer"])

pprint.pprint(sez)