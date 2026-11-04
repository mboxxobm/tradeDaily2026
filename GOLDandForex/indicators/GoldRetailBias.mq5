//+------------------------------------------------------------------+
//| GoldRetailBias.mq5                                               |
//| Retail long% (forexsentiment.live, daily) as oscillator 0-100    |
//| CSV: date_utc,time_utc,datetime_jst,long_pct,short_pct,...       |
//+------------------------------------------------------------------+
#property copyright "tradeDaily2026"
#property version   "1.00"
#property indicator_separate_window
#property indicator_minimum 0
#property indicator_maximum 100
#property indicator_buffers 4
#property indicator_plots   3

#property indicator_label1  "Long%"
#property indicator_type1   DRAW_LINE
#property indicator_color1  clrDodgerBlue
#property indicator_width1  2

#property indicator_label2  "Short%"
#property indicator_type2   DRAW_LINE
#property indicator_color2  clrTomato
#property indicator_width2  1

#property indicator_label3  "Long-50"
#property indicator_type3   DRAW_HISTOGRAM2
#property indicator_color3  clrSilver
#property indicator_width3  2

input string InpFileName      = "GOLD_retail_daily.csv"; // CSV file name
input bool   InpCommonFolder  = false;  // true = Terminal\Common\Files
input int    InpReloadMinutes = 10;     // reload interval (minutes)
input bool   InpShowShort     = false;  // show Short% line
input bool   InpShowHist      = false;  // show histogram of (Long-50), drawn from 50
input double InpLevelHigh     = 70.0;   // upper level
input double InpLevelMid      = 50.0;   // middle level
input double InpLevelLow      = 30.0;   // lower level
input bool   InpAlerts        = false;  // alert on cross of high/low
input bool   InpUseWebRequest = false;  // download CSV via WebRequest (whitelist URL!)
input string InpUrl = "https://raw.githubusercontent.com/mboxxobm/tradeDaily2026/main/GOLDandForex/GOLD_retail_daily.csv";

double BufLong[];
double BufShort[];
double BufHist[];
double BufHistBase[];

datetime g_dates[];
double   g_long[];
double   g_short[];
int      g_count = 0;
datetime g_lastAlertBar = 0;
bool     g_dataChanged = false;

//+------------------------------------------------------------------+
bool DownloadCsv()
  {
   char   post[];
   char   result[];
   string headers;
   ResetLastError();
   int code = WebRequest("GET", InpUrl, "", 10000, post, result, headers);
   if(code != 200)
     {
      PrintFormat("GoldRetailBias: WebRequest failed code=%d err=%d (add https://raw.githubusercontent.com to Tools>Options>Expert Advisors)", code, GetLastError());
      return(false);
     }
   int flags = FILE_WRITE | FILE_BIN;
   if(InpCommonFolder) flags |= FILE_COMMON;
   int h = FileOpen(InpFileName, flags);
   if(h == INVALID_HANDLE) { Print("GoldRetailBias: cannot write ", InpFileName); return(false); }
   FileWriteArray(h, result, 0, ArraySize(result));
   FileClose(h);
   return(true);
  }

//+------------------------------------------------------------------+
bool LoadCsv()
  {
   int flags = FILE_READ | FILE_TXT | FILE_ANSI | FILE_SHARE_READ | FILE_SHARE_WRITE;
   if(InpCommonFolder) flags |= FILE_COMMON;
   ResetLastError();
   int h = FileOpen(InpFileName, flags);
   if(h == INVALID_HANDLE)
     {
      PrintFormat("GoldRetailBias: cannot open %s (err %d). Put it in MQL5\\Files%s", InpFileName, GetLastError(), InpCommonFolder ? " (Common)" : "");
      return(false);
     }
   datetime d[]; double lo[]; double sh[];
   int n = 0;
   bool header = true;
   while(!FileIsEnding(h))
     {
      string line = FileReadString(h);
      StringTrimLeft(line); StringTrimRight(line);
      if(line == "") continue;
      if(header) { header = false; if(StringFind(line, "date") == 0) continue; }
      string f[];
      if(StringSplit(line, ',', f) < 5) continue;
      if(f[3] == "") continue;
      string ds = f[0];
      StringReplace(ds, "-", ".");          // 2025-05-12 -> 2025.05.12
      datetime t = StringToTime(ds);
      if(t <= 0) continue;
      ArrayResize(d, n + 1, 1024); ArrayResize(lo, n + 1, 1024); ArrayResize(sh, n + 1, 1024);
      d[n]  = t;
      lo[n] = StringToDouble(f[3]);
      sh[n] = (f[4] == "") ? 100.0 - lo[n] : StringToDouble(f[4]);
      n++;
     }
   FileClose(h);
   if(n == 0) return(false);
   // CSV is chronological; sort just in case (simple insertion sort)
   for(int i = 1; i < n; i++)
     {
      datetime kd = d[i]; double kl = lo[i]; double ks = sh[i]; int j = i - 1;
      while(j >= 0 && d[j] > kd) { d[j+1] = d[j]; lo[j+1] = lo[j]; sh[j+1] = sh[j]; j--; }
      d[j+1] = kd; lo[j+1] = kl; sh[j+1] = ks;
     }
   bool changed = (n != g_count) || (g_count > 0 && (d[n-1] != g_dates[g_count-1] || lo[n-1] != g_long[g_count-1]));
   ArrayCopy(g_dates, d, 0, 0, WHOLE_ARRAY); ArrayResize(g_dates, n);
   ArrayCopy(g_long, lo, 0, 0, WHOLE_ARRAY); ArrayResize(g_long, n);
   ArrayCopy(g_short, sh, 0, 0, WHOLE_ARRAY); ArrayResize(g_short, n);
   g_count = n;
   if(changed) g_dataChanged = true;
   return(true);
  }

//+------------------------------------------------------------------+
// index of latest date <= day(t), -1 if none (binary search)
int FindIndex(datetime t)
  {
   datetime day = t - (t % 86400);
   int lo = 0, hi = g_count - 1, ans = -1;
   while(lo <= hi)
     {
      int mid = (lo + hi) / 2;
      if(g_dates[mid] <= day) { ans = mid; lo = mid + 1; }
      else hi = mid - 1;
     }
   return(ans);
  }

//+------------------------------------------------------------------+
int OnInit()
  {
   SetIndexBuffer(0, BufLong,  INDICATOR_DATA);
   SetIndexBuffer(1, BufShort, INDICATOR_DATA);
   SetIndexBuffer(2, BufHistBase, INDICATOR_DATA);
   SetIndexBuffer(3, BufHist,  INDICATOR_DATA);
   ArraySetAsSeries(BufLong, false);
   ArraySetAsSeries(BufShort, false);
   ArraySetAsSeries(BufHist, false);
   ArraySetAsSeries(BufHistBase, false);
   for(int p = 0; p < 3; p++) PlotIndexSetDouble(p, PLOT_EMPTY_VALUE, EMPTY_VALUE);
   if(!InpShowShort) PlotIndexSetInteger(1, PLOT_DRAW_TYPE, DRAW_NONE);
   if(!InpShowHist)  PlotIndexSetInteger(2, PLOT_DRAW_TYPE, DRAW_NONE);

   IndicatorSetInteger(INDICATOR_LEVELS, 3);
   IndicatorSetDouble(INDICATOR_LEVELVALUE, 0, InpLevelHigh);
   IndicatorSetDouble(INDICATOR_LEVELVALUE, 1, InpLevelMid);
   IndicatorSetDouble(INDICATOR_LEVELVALUE, 2, InpLevelLow);
   IndicatorSetInteger(INDICATOR_LEVELCOLOR, 0, clrOrangeRed);
   IndicatorSetInteger(INDICATOR_LEVELCOLOR, 1, clrGray);
   IndicatorSetInteger(INDICATOR_LEVELCOLOR, 2, clrLimeGreen);
   IndicatorSetInteger(INDICATOR_LEVELSTYLE, 1, STYLE_DOT);
   IndicatorSetInteger(INDICATOR_DIGITS, 1);
   IndicatorSetString(INDICATOR_SHORTNAME, "GoldRetailBias");

   if(InpUseWebRequest) DownloadCsv();
   LoadCsv();
   EventSetTimer(MathMax(60, InpReloadMinutes * 60));
   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason) { EventKillTimer(); }

void OnTimer()
  {
   if(InpUseWebRequest) DownloadCsv();
   LoadCsv();
   if(g_dataChanged) ChartSetSymbolPeriod(0, _Symbol, _Period); // forces full recalculation
  }

//+------------------------------------------------------------------+
int OnCalculate(const int rates_total, const int prev_calculated,
                const datetime &time[], const double &open[], const double &high[],
                const double &low[], const double &close[], const long &tick_volume[],
                const long &volume[], const int &spread[])
  {
   ArraySetAsSeries(time, false);
   int start = prev_calculated - 1;
   if(start < 0 || g_dataChanged) { start = 0; g_dataChanged = false; }
   for(int i = start; i < rates_total; i++)
     {
      int k = (g_count > 0) ? FindIndex(time[i]) : -1;
      if(k < 0) { BufLong[i] = EMPTY_VALUE; BufShort[i] = EMPTY_VALUE; BufHist[i] = EMPTY_VALUE; BufHistBase[i] = EMPTY_VALUE; continue; }
      BufLong[i]  = g_long[k];
      BufShort[i] = g_short[k];
      BufHistBase[i] = InpLevelMid;              // histogram from mid level (50) to long%
      BufHist[i]     = g_long[k];
     }
   // alerts on last closed bar vs previous
   if(InpAlerts && rates_total > 2)
     {
      int c = rates_total - 1, p = rates_total - 2;
      if(time[c] != g_lastAlertBar && BufLong[c] != EMPTY_VALUE && BufLong[p] != EMPTY_VALUE)
        {
         if(BufLong[p] <= InpLevelHigh && BufLong[c] > InpLevelHigh)
           { Alert(_Symbol, " GoldRetailBias: Long% crossed above ", InpLevelHigh, " (", BufLong[c], ")"); g_lastAlertBar = time[c]; }
         else if(BufLong[p] >= InpLevelLow && BufLong[c] < InpLevelLow)
           { Alert(_Symbol, " GoldRetailBias: Long% crossed below ", InpLevelLow, " (", BufLong[c], ")"); g_lastAlertBar = time[c]; }
        }
     }
   return(rates_total);
  }
//+------------------------------------------------------------------+
