CFLAGS_uniwill-acpi.o := -DDEBUG
CFLAGS_uniwill-wmi.o := -DDEBUG
obj-m += uniwill-laptop.o
uniwill-laptop-y := uniwill-acpi.o uniwill-wmi.o

# Default to the running kernel, but let DKMS point at whichever kernel it is
# building for. Without this, a DKMS build for a kernel other than the running
# one silently targets the wrong headers.
KDIR ?= /lib/modules/$(shell uname -r)/build

all:
	$(MAKE) -C $(KDIR) M=$(CURDIR) modules

clean:
	$(MAKE) -C $(KDIR) M=$(CURDIR) clean

.PHONY: all clean
