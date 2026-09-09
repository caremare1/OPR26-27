print("Hello world!")

# Integer - cela števila
x = 10
y = -10

#operacije Inta
print(x+y)
print

#celoštevilško deljenje (//)
print(x//y)
print(int(x//y))

# deljenje z ostankom 
print(x%2)

print(type(1.2))

# spreminjane tipa (parse)

x = "12"
x = int(x) # float(x), str(x)
print(x+1)

print(0.1 + 0.2 == 0.3)


# string - nizi znakov
a = "abc"
b = "def"

print(a+b)

# indeksiranje

print(a[0])
#print(a[10])
# rezine/slice
# string[od:do:korak]

print(a[0:1])
print(a[::-1]) # obrnjen string
print(len(a)) # dolžina stringa
# f-string
ime = "anja"
print(f"Pozdrvljen {ime}")