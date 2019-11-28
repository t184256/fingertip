def main(m):
    with m:
        m.unseal()
        return m
