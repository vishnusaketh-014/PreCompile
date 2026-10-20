CC ?= gcc
CFLAGS ?= -O2 -Wall -I.
TARGET = sample_bin
OBJS = main.o compute.o util.o

all: $(TARGET)

$(TARGET): $(OBJS)
	$(CC) $(CFLAGS) -o $@ $(OBJS)

%.o: %.c project.h
	$(CC) $(CFLAGS) -c $< -o $@

clean:
	rm -f $(OBJS) $(TARGET)

.PHONY: all clean
