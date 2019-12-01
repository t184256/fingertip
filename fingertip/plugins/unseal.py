def main(m):
    if m.sealed:
        with m:
            m.unseal()
            return m
