library ieee;
  use ieee.std_logic_1164.all;
  use ieee.numeric_std.all;
  use std.env.finish;

entity sequential_16bit_en_tb is
  generic (
    -- Path to the bitstream produced by bit_gen. Override at elaboration
    -- (nvc -gBITSTREAM_FILE=...) or at run time (ghdl -r ... -gBITSTREAM_FILE=...).
    BITSTREAM_FILE : string := "build/sequential_16bit_en.bin";
    -- Upper bound on the buffer only, not the bitstream length: the actual
    -- length is counted while reading the file.
    BITSTREAM_BUF_BYTES : integer := 65536
  );
end entity sequential_16bit_en_tb;

architecture Behavior of sequential_16bit_en_tb is

  component eFPGA_top is
    generic (
      FrameBitsPerRow  : integer := 32;
      FrameSelectWidth : integer := 5;
      MaxFramesPerCol  : integer := 20;
      NumberOfCols     : integer := 10;
      NumberOfRows     : integer := 14;
      RowSelectWidth   : integer := 5;
      desync_flag      : integer := 20;
      include_eFPGA    : integer := 1
    );
    port (
      I_top          : out   std_logic_vector(NumberOfRows * 2 - 1 downto 0);
      T_top          : out   std_logic_vector(NumberOfRows * 2 - 1 downto 0);
      O_top          : in    std_logic_vector(NumberOfRows * 2 - 1 downto 0);
      A_config_C     : out   std_logic_vector(NumberOfRows * 4 - 1 downto 0);
      B_config_C     : out   std_logic_vector(NumberOfRows * 4 - 1 downto 0);
      Config_accessC : out   std_logic_vector( 55 downto 0);

      CLK             : in    std_logic;
      resetn          : in    std_logic;
      ComActive       : out   std_logic;
      ReceiveLED      : out   std_logic;
      Rx              : in    std_logic;
      s_clk           : in    std_logic;
      s_data          : in    std_logic;
      SelfWriteData   : in    std_logic_vector(31 downto 0);
      SelfWriteStrobe : in    std_logic
    );
  end component eFPGA_top;

  component sequential_16bit_en is
    port (
      clk    : in    std_logic;
      io_out : out   std_logic_vector(27 downto 0);
      io_oeb : out   std_logic_vector(27 downto 0);
      io_in  : in    std_logic_vector(27 downto 0)
    );
  end component sequential_16bit_en;

  signal I_top         : std_logic_vector( 27 downto 0);
  signal T_top         : std_logic_vector(27 downto 0);
  signal O_top         : std_logic_vector( 27 downto 0);
  signal A_cfg         : std_logic_vector(55 downto 0);
  signal B_cfg         : std_logic_vector(55 downto 0);
  signal Config_access : std_logic_vector(55 downto 0);

  signal I_top_gold : std_logic_vector(27 downto 0);
  signal T_top_gold : std_logic_vector(27 downto 0);
  signal oeb_gold   : std_logic_vector(27 downto 0);

  signal CLK             : std_logic;
  signal resetn          : std_logic;
  signal SelfWriteStrobe : std_logic;
  signal SelfWriteData   : std_logic_vector(31 downto 0);
  signal ComActive       : std_logic;
  signal Rx              : std_logic;
  signal s_clk           : std_logic;
  signal s_data          : std_logic;
  signal ReceiveLED      : std_logic;

  type bitstream_Type is array (0 to BITSTREAM_BUF_BYTES - 1) of std_logic_vector(7 downto 0);

  -- Reading the bitstream one character at a time gives the raw bytes of the
  -- binary produced by bit_gen, so no intermediate text format is needed.

  type byte_file is file of character;

  signal bitstream : bitstream_Type;

  procedure read_bitstream (
    filename :     in string;
    bs       : out bitstream_Type;
    length   : out natural
  ) is

    file     read_file : byte_file;
    variable c         : character;
    variable counter   : natural;

  begin

    counter := 0;
    bs      := (others => x"00");
    file_open(read_file, filename, read_mode);

    while not endfile(read_file) loop

      -- Truncating here would silently configure the fabric with a partial
      -- bitstream, so fail loudly instead.
      assert counter < BITSTREAM_BUF_BYTES
        report "Bitstream exceeds BITSTREAM_BUF_BYTES; raise the generic."
        severity failure;
      read(read_file, c);
      bs(counter) := std_logic_vector(to_unsigned(character'pos(c), 8));
      counter     := counter + 1;

    end loop;

    file_close(read_file);
    length := counter;

  end procedure read_bitstream;

begin

  init_efpga : component eFPGA_top
    generic map (
      FrameBitsPerRow  => 32,
      FrameSelectWidth => 5,
      MaxFramesPerCol  => 20,
      NumberOfCols     => 10,
      NumberOfRows     => 14,
      RowSelectWidth   => 5,
      desync_flag      => 20,
      include_eFPGA    => 1
    )
    port map (
      I_top          => I_top,
      T_top          => T_top,
      O_top          => O_top,
      A_config_C     => A_cfg,
      B_config_C     => B_cfg,
      Config_accessC => Config_access,

      CLK             => CLK,
      resetn          => resetn,
      SelfWriteStrobe => SelfWriteStrobe,
      SelfWriteData   => SelfWriteData,

      Rx         => Rx,
      ComActive  => ComActive,
      ReceiveLED => ReceiveLED,
      s_clk      => s_clk,
      s_data     => s_data

    );

  init_top : component sequential_16bit_en
    port map (
      clk    => CLK,
      io_out => I_top_gold,
      io_oeb => oeb_gold,
      io_in  => O_top
    );

  T_top_gold <= not oeb_gold;

  -- Quiescent stimulus inputs (never change during the test)
  Rx     <= '1';
  s_clk  <= '0';
  s_data <= '0';

  process is
  begin

    CLK <= '0';
    wait for 5000 ps;

    loop

      CLK <= not CLK;
      wait for 5000 ps;

    end loop;

  end process;

  process is

    variable i               : integer;
    variable bs              : bitstream_Type;
    variable bitstream_bytes : natural;

  begin

    resetn          <= '0';
    SelfWriteStrobe <= '0';
    SelfWriteData   <= (others => '0');

    i         := 0;
    read_bitstream(BITSTREAM_FILE, bs, bitstream_bytes);
    bitstream <= bs;
    wait for 100 ps;
    report "Read " & integer'image(bitstream_bytes) & " bitstream bytes from " & BITSTREAM_FILE;
    resetn    <= '0';
    wait for 10000 ps;
    resetn    <= '1';
    wait for 10000 ps;

    for Verilog_Repeat in 1 to 20 loop

      wait until rising_edge(CLK);

    end loop;

    wait for 2500 ps;

    while i < bitstream_bytes loop

      SelfWriteData <= bitstream(i) & bitstream(i + 1) & bitstream(i + 2) & bitstream(i + 3);
      -- wait 2 clock cycles
      wait until rising_edge(CLK);
      wait until rising_edge(CLK);

      -- report integer'image(i) & " " & to_hstring(SelfWriteData);
      SelfWriteStrobe <= '1';
      wait until rising_edge(CLK);
      SelfWriteStrobe <= '0';

      -- wait another 2 clock cycles
      wait until rising_edge(CLK);
      wait until rising_edge(CLK);

      i := i + 4;

    end loop;

    -- wait 100 clock cycles
    for Verilog_Repeat in 1 to 100 loop

      wait until rising_edge(CLK);

    end loop;

    report "Configuration completed";

    -- reset user design
    O_top <= b"0000_0000_0000_0000_0000_0000_0011";

    -- wait 5 clock cycles
    for Verilog_Repeat in 1 to 5 loop

      wait until rising_edge(CLK);

    end loop;

    -- start user design
    O_top <= b"0000_0000_0000_0000_0000_0000_0010";
    wait until rising_edge(CLK);

    for Verilog_Repeat in 0 to 100 loop

      wait until falling_edge(CLK);
      report "fabric = " & integer'image(To_Integer(unsigned(I_top))) &
             " gold = " & integer'image(To_Integer(unsigned(I_top_gold)));

      if (To_integer(unsigned(I_top)) /= To_integer(unsigned(I_top_gold))) then
        report "Error: Missmatch between fabric output and golden reference."
          severity error;
      end if;

    end loop;

    report "SIMULATION FINISHED";

    wait for 5000 ps;
    finish;

  end process;

end architecture Behavior;
