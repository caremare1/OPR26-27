#indeksi
import requests

imena = ["Luka","Jaka","Bine"]

#for each
#for i in imena:
    #print(i)

# enumerate
#print(list(enumerate(imena)))
base_url = "https://api.agify.io/"
output = requests.get(base_url).json()

#for i in enumerate(imena):
#    print(i, imena)

for i in imena:
    requests1 = requests.get(base_url, params= {"name" = i})
    podatki = response..json()

print(podatki)
