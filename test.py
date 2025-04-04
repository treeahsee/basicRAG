f = open("urls.txt", "r")

# urls = [url for url in f.readlines()]
# print(urls)
urls = []
with open("urls.txt") as f:
    urls = f.read().splitlines()