from tomlkit import comment, document

metadata = document()
metadata.add(comment("don't modify this file"))
metadata.add('version', '0.8.3')
