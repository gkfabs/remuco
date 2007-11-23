#ifndef COMMON_H_
#define COMMON_H_

///////////////////////////////////////////////////////////////////////////////
//
// includes
//
///////////////////////////////////////////////////////////////////////////////

#include <remuco.h>

///////////////////////////////////////////////////////////////////////////////
//
// utility constans
//
///////////////////////////////////////////////////////////////////////////////

///////////////////////////////////////////////////////////////////////////////
//
// utility macros
//
///////////////////////////////////////////////////////////////////////////////

////////// a sleep function with milli seconds //////////

#define g_msleep(_ms)		g_usleep((_ms) * 1000) 

////////// an 'if-then' boolean expression //////////

#define concl(_a, _b)		((!(_a)) || ((_a) && (_b)))

////////// assertions //////////

#if LOGLEVEL >= LL_DEBUG
#define g_assert_debug(_expr)	g_assert(_expr)
#define g_assert_not_reached_debug() g_assert_not_reached()
#else
#define g_assert_debug(_expr)
#define g_assert_not_reached_debug()
#endif

///////////////////////////////////////////////////////////////////////////////
//
// debug functions
//
///////////////////////////////////////////////////////////////////////////////

////////// dump macros used by remuco data types //////////

#define REM_DATA_DUMP_HDR(_t, _p) \
			GString *_dump = g_string_sized_new(500);	\
			g_string_printf(_dump, "DUMP(%s@%p):\n", _t, _p);

#define REM_DATA_DUMP_FS(args...) \
			g_string_printf(_dump, ##args);

#define REM_DATA_DUMP_FTR \
			LOG_DEBUG("%s", _dump->str);	\
			g_string_free(_dump, TRUE);

////////// dump binary data //////////

#ifdef DO_LOG_NOISE
static void
rem_dump_ba(GByteArray *ba)
{
	LOG_NOISE("called\n");
	
	GString	*dump;
	guint	u;
	guint8	*walker, *ba_end;

	dump = g_string_sized_new(ba->len * 4);
	
	g_string_printf(dump, "Binary Data: %p (%u bytes)", ba->data, ba->len);
	ba_end = ba->data + ba->len;
	for (u = 0, walker = ba->data; walker < ba_end; u = (u+1) % 16, walker++) {
		if (u == 0) {
			g_string_append(bin, "\n");			
		}
		g_string_printf(dump, "%02hhX ", *walker);
	}
	
	LOG_NOISE("%s", dump);
	
}
static void
rem_dump(guint8 *data, guint len)
{
	GByteArray ba;
	ba.data = data;
	ba.len = len;
	rem_dump_ba(&ba);
}
#else
#define rem_dump_ba(_ba)
#define rem_dump(_data, _len)
#endif

#endif /*COMMON_H_*/