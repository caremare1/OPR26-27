#slovarji (dictionary)

# slovar = {"ključ" : "vrednost",
         # "ključ2" : "vrednost2"}

# print(slovar)
#dostop
# print(slovar["ključ2"])

# raznoliki slovar
razno = {"stevilo" : 6,
         "ime" : "Marko",
         "seznam" : [1,2,3,4],
         "slovar" : {"firma" : "Audi", "moč" : "120kW"}}
print(razno["stevilo"] + 10)
print(max(razno["seznam"]))
print(razno["slovar"]) # {"firma" : "Audi", "moč" : "120kW"}
print(razno["slovar"]["firma"])
print(razno["slovar"]["moč"])

# Open Meteo API
import requests
base_url = "https://api.open-meteo.com/v1/forecast?latitude=52.52&longitude=13.41&daily=rain_sum&timezone=Europe%2FBerlin"

call = requests.get(base_url).json()
print(call["daily"]["rain_sum"][2])
