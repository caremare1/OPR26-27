#slovarji (dictionary)

# slovar = {"ključ" : "vrednost",
         # "ključ2" : "vrednost2"}

# print(slovar)
#dostop
# print(slovar["ključ2"])

# raznoliki slovar
# razno = {"stevilo" : 6,
         # "ime" : "Marko",
         # "seznam" : [1,2,3,4],
         # "slovar" : {"firma" : "Audi", "moč" : "120kW"}}
# print(razno["stevilo"] + 10)
# print(max(razno["seznam"]))
# print(razno["slovar"]) # {"firma" : "Audi", "moč" : "120kW"}
# print(razno["slovar"]["firma"])
# print(razno["slovar"]["moč"])

# Open Meteo API
import requests
#base_url = "https://api.open-meteo.com/v1/forecast?latitude=52.52&longitude=13.41&daily=rain_sum&timezone=Europe%2FBerlin"

#call = requests.get(base_url).json()
#print(call["daily"]["rain_sum"][2])
#print(call["daily"]["time"])

#base_url2 = "https://api.open-meteo.com/v1/forecast?latitude=52.52&longitude=13.41&current=temperature_2m&timezone=Europe%2FBerlin&forecast_days=1"

#call2 = requests.get(base_url2).json()
#print(call2["current"]["temperature_2m"]) # trenutna temperatura

#base_url3 = "https://api.open-meteo.com/v1/forecast?latitude=52.52&longitude=13.41&daily=temperature_2m_max,temperature_2m_min&timezone=Europe%2FBerlin"

#call3 = requests.get(base_url3).json()
#print(call3["daily"]["temperature_2m_max"]) # za cel teden najvišja temperatura
#print(call3["daily"]["temperature_2m_min"]) # za cel teden najnižja temperatura

#base_url4 = "https://api.open-meteo.com/v1/forecast?latitude=52.52&longitude=13.41&daily=temperature_2m_min&timezone=Europe%2FBerlin"

#call4 = requests.get(base_url4).json()
#print(min(call4["daily"]["temperature_2m_min"])) # najnižja temperatura v tednu
#min(call4["daily"]["temperature_2m_min"]))
#print()

#base_url5 = "https://api.open-meteo.com/v1/forecast?latitude=52.52&longitude=13.41&daily=temperature_2m_max&timezone=Europe%2FBerlin"

#call5 = requests.get(base_url5).json()
#print(max(call5["daily"]["temperature_2m_max"])) # Najvišja tempreratura v tednu

#base_url6 = "https://api.open-meteo.com/v1/forecast?latitude=52.52&longitude=13.41&daily=temperature_2m_max,temperature_2m_min&timezone=Europe%2FBerlin"

#call6 = requests.get(base_url6).json()
