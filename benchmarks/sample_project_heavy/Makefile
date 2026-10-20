CC ?= gcc
CFLAGS ?= -O2 -Wall

all: big_prog

big_prog: big.o
	$(CC) $(CFLAGS) -o big_prog big.o

big.o: big.c
	$(CC) $(CFLAGS) -c big.c -o big.o

clean:
	rm -f big.o big_prog
